"""No-network tests of run_describe persistence and the hard spend stop. Run: .venv/bin/python dev/test_describe_persist.py"""
import argparse, asyncio, json, os, signal, sqlite3, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import build_explore as be

N = 60
PRICE = {"input_per_m": 100.0, "output_per_m": 500.0}  # 1000 in + 100 out = $0.15 per call


def make_db(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    decls = d / "decls.jsonl"
    with decls.open("w") as f:
        for i in range(N):
            f.write(json.dumps({"name": f"T.thm{i}", "kind": "theorem", "module": "T", "file": "T.lean", "line": i,
                                "signature": f"theorem T.thm{i} : {i} = {i}", "type_full": f"{i} = {i}",
                                "docstring": None, "is_mathlib": False, "hash": f"h{i}"}) + "\n")
    out = d / "out"
    a = argparse.Namespace(decls=decls, index_root=d, pin="t-0", tauceti_sha="x", mathlib_rev="y", names=None, limit=None,
                           skip_kinds="", force=True, tauceti_repo_url="http://x")
    be.stage_adapt(a, out)
    return out / "lean_explore.db"


def todo_layers(db):
    con = be.connect(db)
    todo = [dict(r) for r in con.execute("SELECT id,name,source_text,docstring,dependencies FROM declarations WHERE informalization IS NULL ORDER BY id")]
    con.close()
    for t in todo:
        t["deps"] = []
    return [todo]


def args(**kw):
    base = dict(informalize_model="m", informalize_max_concurrent=4, commit_every=1, max_spend=None, max_tokens=100,
                base_url="", timeout=1, est_output_tokens=1)
    base.update(kw)
    return argparse.Namespace(**base)


async def stub(prompt):
    await asyncio.sleep(0)
    return "desc", {"prompt_tokens": 1000, "completion_tokens": 100}, "stop"


def count(db):
    con = sqlite3.connect(db)
    n = con.execute("SELECT count(*) FROM declarations WHERE informalization IS NOT NULL").fetchone()[0]
    con.close()
    return n


def run(db, a, caller):
    return asyncio.run(be.run_describe(a, db, todo_layers(db), {}, be.load_prompt_template(), "k", PRICE, caller=caller))


def main():
    with tempfile.TemporaryDirectory() as t:
        # 1. hard stop at cap: $0.15/call, cap $1.00 -> stops after >=7 calls, in-flight (<=4 more) persisted, every paid row persisted
        db = make_db(Path(t) / "a")
        try:
            run(db, args(max_spend=1.0), stub); raise SystemExit("expected non-zero exit")
        except SystemExit as e:
            assert e.code == 3, e.code
        log = [json.loads(l) for l in (db.parent / "describe-log.jsonl").read_text().splitlines()]
        paid = [l for l in log if l.get("prompt_tokens")]
        assert count(db) == len(paid), (count(db), len(paid))
        assert 7 <= len(paid) <= 7 + 4, len(paid)
        assert log[-1]["stop"].startswith("actual spend")
        print("cap stop ok:", len(paid), "paid rows, all persisted; spend", log[-1]["summary"]["usd"])
        # resume to completion with no cap
        run(db, args(), stub)
        assert count(db) == N
        print("resume to completion ok:", count(db))
        # 2. credit error stops cleanly
        db2 = make_db(Path(t) / "b")
        class E(Exception):
            status_code = 402
        n = {"i": 0}
        async def credit(prompt):
            n["i"] += 1
            if n["i"] > 5:
                raise E("insufficient credits")
            return await stub(prompt)
        try:
            run(db2, args(informalize_max_concurrent=1), credit); raise SystemExit("expected exit")
        except SystemExit as e:
            assert e.code == 3
        assert count(db2) == 5, count(db2)
        print("credit stop ok:", count(db2))
    # 3. SIGKILL after N rows: subprocess, commit_every=1 -> exactly the rows reported done are persisted
    with tempfile.TemporaryDirectory() as t:
        db = make_db(Path(t))
        child = f'''
import sys, asyncio, os
sys.path.insert(0, {str(HERE)!r}); sys.path.insert(0, {str(HERE / "dev")!r})
import test_describe_persist as T, build_explore as be
from pathlib import Path
db = Path({str(db)!r}); n = {{"i": 0}}
async def s(prompt):
    n["i"] += 1
    if n["i"] > 17: os.kill(os.getpid(), 9)
    return await T.stub(prompt)
T.run(db, T.args(informalize_max_concurrent=1), s)
'''
        subprocess.run([sys.executable, "-c", child])
        k = count(db)
        assert k == 17, k
        lines = len((db.parent / "describe-log.jsonl").read_text().splitlines())
        assert lines == 17, lines
        run(db, args(), stub)
        assert count(db) == N
        print("kill after 17: exactly 17 persisted (+17 log lines); resume completes:", count(db))
    print("ALL OK")


if __name__ == "__main__":
    main()
