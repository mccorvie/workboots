#!/usr/bin/env python3
"""Build a stand-in ``decls.jsonl`` slice in the S0 contract schema.

S0 (the shared doc-gen4 extraction) had not landed when this service was
written, so this script fabricates a small, honest slice from two sources:

* Mathlib rows: taken from LeanExplore's prebuilt database (upstream
  ``lean_explore.db``), which gives real names, modules, docstrings and
  source text. The ``signature`` is the statement part of the source text
  (everything before ``:=`` / ``where`` / ``by``), the file/line come from
  ``source_link``. These rows carry upstream descriptions, so they exercise
  the description-reuse path.
* TauCeti rows: a regex scan of a TauCeti checkout (namespace tracking,
  docstring capture, statement up to ``:=``). Crude, but good enough to
  exercise the "no description available" path and the pilot selector.

Contract schema (workboots-plan.md, "Index artefacts"):
    name, kind, module, file, line, signature, docstring, is_mathlib, hash

Nothing here is a substitute for S0's output; it only lets the adapt, embed
and index stages run end to end on CPU before the real extraction exists.
Run with ``python3 -I`` (reads untrusted downloaded data).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sqlite3
import sys
from pathlib import Path

KINDS = ("theorem", "lemma", "def", "abbrev", "structure", "class", "instance",
         "inductive", "opaque", "axiom")
DECL_RE = re.compile(
    r"^(?P<attrs>(?:@\[[^\]]*\]\s*)*)"
    r"(?P<mods>(?:(?:private|protected|noncomputable|nonrec|scoped|local)\s+)*)"
    r"(?P<kind>theorem|lemma|def|abbrev|structure|class|instance|inductive|opaque|axiom)\s+"
    r"(?P<name>[^\s:({\[⦃]+)?"
)
STOP_RE = re.compile(r"(:=|\bwhere\b|\bby\b|\|)")


def in_block_comment(lines: list[str], idx: int) -> bool:
    """True if line idx sits inside an unterminated /- ... -/ block (cheap scan from the top)."""
    depth = 0
    for line in lines[:idx]:
        depth += line.count("/-") - line.count("-/")
    return depth > 0


def _hash(name: str, signature: str, docstring: str | None) -> str:
    h = hashlib.sha256()
    h.update(name.encode()); h.update(b"\0")
    h.update(signature.encode()); h.update(b"\0")
    h.update((docstring or "").encode())
    return h.hexdigest()[:16]


def _statement(source: str) -> str:
    """Return the declaration head (up to the first := / where / by at depth 0)."""
    # Drop a leading doc comment and attributes so the signature starts at the keyword.
    s = re.sub(r"\A\s*/--.*?-/\s*", "", source, flags=re.S)
    s = re.sub(r"\A(?:@\[[^\]]*\]\s*)+", "", s)
    depth = 0
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch in "([{⦃":
            depth += 1
        elif ch in ")]}⦄":
            depth -= 1
        if depth == 0:
            m = STOP_RE.match(s, i)
            if m and (m.group(0) != "|" or out and out[-1] == "\n"):
                break
        out.append(ch)
        i += 1
    stmt = "".join(out).rstrip()
    return re.sub(r"\n\s*\n+", "\n", stmt)


def _kind_from_source(source: str) -> str:
    s = re.sub(r"\A\s*/--.*?-/\s*", "", source, flags=re.S)
    s = re.sub(r"\A(?:@\[[^\]]*\]\s*)+", "", s)
    s = re.sub(r"\A(?:(?:private|protected|noncomputable|nonrec|scoped|local)\s+)+", "", s)
    m = re.match(r"(theorem|lemma|def|abbrev|structure|class|instance|inductive|opaque|axiom)\b", s)
    return m.group(1) if m else "def"


def mathlib_rows(db: Path, n: int, seed: int, module_like: str) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT name, module, docstring, source_text, source_link FROM declarations "
        "WHERE module LIKE ? AND name NOT LIKE '%.mk' ORDER BY id",
        (module_like,),
    ).fetchall()
    rnd = random.Random(seed)
    picked = rnd.sample(rows, min(n, len(rows)))
    out = []
    for r in picked:
        m = re.search(r"/blob/[0-9a-f]+/(.+\.lean)#L(\d+)", r["source_link"])
        file, line = (m.group(1), int(m.group(2))) if m else ("", 0)
        sig = _statement(r["source_text"])
        if not sig.strip():
            continue
        out.append({
            "name": r["name"],
            "kind": _kind_from_source(r["source_text"]),
            "module": r["module"],
            "file": file,
            "line": line,
            "signature": sig,
            "docstring": r["docstring"],
            "is_mathlib": True,
            "hash": _hash(r["name"], sig, r["docstring"]),
        })
    return out


def tauceti_rows(repo: Path, n: int, seed: int) -> list[dict]:
    files = sorted((repo / "TauCeti").rglob("*.lean"))
    rnd = random.Random(seed)
    rnd.shuffle(files)
    out: list[dict] = []
    for f in files:
        if len(out) >= n:
            break
        text = f.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        ns: list[str] = []
        pending_doc: str | None = None
        i = 0
        while i < len(lines):
            line = lines[i]
            m = re.match(r"^namespace\s+(\S+)", line)
            if m:
                ns.append(m.group(1)); pending_doc = None; i += 1; continue
            m = re.match(r"^end\s+(\S+)", line)
            if m and ns and ns[-1] == m.group(1):
                ns.pop(); pending_doc = None; i += 1; continue
            if line.startswith("/--"):
                j = i
                buf = []
                while j < len(lines):
                    buf.append(lines[j])
                    if "-/" in lines[j]:
                        break
                    j += 1
                doc = "\n".join(buf)
                doc = re.sub(r"\A/--\s*", "", doc); doc = re.sub(r"\s*-/\s*\Z", "", doc)
                pending_doc = doc.strip()
                i = j + 1
                continue
            m = DECL_RE.match(line)
            if m and m.group("name") and re.fullmatch(r"[A-Za-z_«][\w'.!?»]*", m.group("name")) and not in_block_comment(lines, i):
                # Collect the head: continuation lines until a stop token at depth 0.
                head_lines = [line]
                j = i + 1
                while j < len(lines) and not STOP_RE.search("\n".join(head_lines)) and j - i < 25:
                    head_lines.append(lines[j]); j += 1
                head = "\n".join(head_lines)
                head = re.sub(r"\A(?:@\[[^\]]*\]\s*)+", "", head)
                sig = _statement(head)
                name = m.group("name")
                if m.group("kind") == "instance" and (":" in name or name.startswith("[")):
                    i += 1; pending_doc = None; continue
                full = ".".join(ns + [name]) if ns and not name.startswith("_root_.") else name.removeprefix("_root_.")
                rel = f.relative_to(repo).as_posix()
                module = rel[:-5].replace("/", ".")
                out.append({
                    "name": full,
                    "kind": m.group("kind"),
                    "module": module,
                    "file": rel,
                    "line": i + 1,
                    "signature": sig,
                    "docstring": pending_doc,
                    "is_mathlib": False,
                    "hash": _hash(full, sig, pending_doc),
                })
                pending_doc = None
                if len(out) >= n:
                    break
            elif line.strip():
                pending_doc = None
            i += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--upstream-db", type=Path, required=True)
    ap.add_argument("--tauceti-repo", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True, help="…/tauceti-index/<pin>/ (decls.jsonl + MANIFEST.json written here)")
    ap.add_argument("--pin", required=True)
    ap.add_argument("--tauceti-sha", required=True)
    ap.add_argument("--mathlib-rev", required=True)
    ap.add_argument("--lean-toolchain", required=True)
    ap.add_argument("--n-mathlib", type=int, default=120)
    ap.add_argument("--n-tauceti", type=int, default=80)
    ap.add_argument("--mathlib-module-like", default="Mathlib.Topology.%")
    ap.add_argument("--seed", type=int, default=20261008)
    a = ap.parse_args()

    rows = mathlib_rows(a.upstream_db, a.n_mathlib, a.seed, a.mathlib_module_like)
    rows += tauceti_rows(a.tauceti_repo, a.n_tauceti, a.seed)
    seen = set(); uniq = []
    for r in rows:
        if r["name"] in seen:
            continue
        seen.add(r["name"]); uniq.append(r)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    decls = a.out_dir / "decls.jsonl"
    with decls.open("w", encoding="utf-8") as fh:
        for r in uniq:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    sha = hashlib.sha256(decls.read_bytes()).hexdigest()
    manifest = {
        "batch": a.pin.split("-")[0],
        "pin": a.pin,
        "tauceti_sha": a.tauceti_sha,
        "mathlib_rev": a.mathlib_rev,
        "lean_toolchain": a.lean_toolchain,
        "docgen4_rev": None,
        "built_at": None,
        "counts": {
            "total": len(uniq),
            "mathlib": sum(1 for r in uniq if r["is_mathlib"]),
            "tauceti": sum(1 for r in uniq if not r["is_mathlib"]),
            "by_kind": {k: sum(1 for r in uniq if r["kind"] == k) for k in KINDS if any(r["kind"] == k for r in uniq)},
        },
        "sha256": {"decls.jsonl": sha},
        "SAMPLE": "STAND-IN SLICE from dev/make_sample_decls.py; not an S0 extraction",
    }
    (a.out_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest["counts"]), file=sys.stderr)
    print(decls)


if __name__ == "__main__":
    main()
