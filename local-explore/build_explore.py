#!/usr/bin/env python3
"""Build a frozen LeanExplore index for one TauCeti pin from the shared extraction.

    build_explore.py <pin> --stage {adapt,describe,embed,index,export,all} [options]

Input : ~/.local/share/tauceti-index/<pin>/decls.jsonl  (S0 contract schema:
        name, kind, module, file, line, signature, docstring, is_mathlib, hash;
        an optional "deps" list is used when present)
Output: ~/.local/share/tauceti-explore/<pin>/   in LeanExplore's own data layout
        (lean_explore.db, informalization_faiss.index, informalization_faiss_ids_map.json,
        bm25_ids_map.json, bm25_name_raw/, bm25_name_spaced/) plus MANIFEST.json and
        describe-log.jsonl.

Route: "adapter". LeanExplore's parse stage is bypassed; decls.jsonl is written straight
into its SQLite schema (table `declarations`, created from lean_explore.models.Base so it
cannot drift), with a side table `tauceti_meta` for kind / is_mathlib / hash / provenance.
The describe stage is reimplemented here (same prompt, same dependency-context rules,
same (name, source_text) reuse semantics plus hash- and name-keyed reuse) because the
PyPI 1.3.0 wheel does not ship prompt.txt and because we need a dry-run cost estimate
and a spend guard. The embed and index stages reuse LeanExplore's clients and BM25
builder; the FAISS builder is replicated with a flat-index branch for small slices.

Spend guard: the describe stage never calls the LLM unless OPENROUTER_API_KEY is set
AND --i-approve-spend is passed; it always prints the estimate first. --dry-run makes
no network calls at all.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import hashlib
import importlib.metadata
import json
import logging
import math
import os
import re
import sqlite3
import struct
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
log = logging.getLogger("build_explore")

DEFAULT_INDEX_ROOT = Path.home() / ".local/share/tauceti-index"
DEFAULT_EXPLORE_ROOT = Path.home() / ".local/share/tauceti-explore"

EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
EMBEDDING_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"  # HF main on 2026-10-08
RERANKER_MODEL = "Qwen/Qwen3-Reranker-0.6B"
RERANKER_REVISION = "e61197ed45024b0ed8a2d74b80b4d909f1255473"  # HF main on 2026-10-08
DEFAULT_DESCRIBER = "google/gemini-3-flash-preview"  # LeanExplore's default
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Mirrors lean_explore.extract.informalize (1.3.0) exactly.
MAX_PROMPT_DEPENDENCIES = 20
MAX_DEPENDENCY_DESCRIPTION_LENGTH = 256
DESCRIBE_TEMPERATURE = 0.3

# FAISS: LeanExplore uses IVFFlat with nlist = max(256, sqrt(n)); FAISS refuses to train
# with fewer points than clusters and warns below 39 points per cluster, so small
# slices get an exact flat index instead (same metric, same query code path).
FAISS_FLAT_BELOW = 256 * 39

TAUCETI_REPO_URL = "https://github.com/TauCetiProject/TauCeti"
MATHLIB_REPO_URL = "https://github.com/leanprover-community/mathlib4"

LEAN_KEYWORDS = ("theorem", "lemma", "def", "abbrev", "structure", "class", "instance",
                 "inductive", "opaque", "axiom", "example")
IDENT_RE = re.compile(r"[A-Za-z_Ͱ-Ͽἀ-῿℀-⅏][\w'!?Ͱ-Ͽἀ-῿℀-⅏₀-ₜ]*(?:\.[A-Za-z_Ͱ-Ͽ][\w'!?Ͱ-Ͽ₀-ₜ]*)*")


# --------------------------------------------------------------------------- helpers

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_prompt_template() -> str:
    text = (HERE / "prompt.txt").read_text(encoding="utf-8")
    head, _, body = text.partition("\n\n")
    if all(line.startswith("# ") for line in head.splitlines()):
        return body
    return text


def load_prices(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("models", {})


def connect(db: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(db)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    con.row_factory = sqlite3.Row
    return con


def has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def norm_ws(s: str | None) -> str:
    return " ".join((s or "").split())


class TokenCounter:
    """Token counter for cost estimates. Uses the Qwen3 tokenizer from the pinned
    embedding snapshot when it is present locally (no download), else chars/3.4."""

    def __init__(self, snapshot: Path | None):
        self.tok = None
        self.kind = "chars/3.4"
        if snapshot and snapshot.exists():
            try:
                from transformers import AutoTokenizer  # type: ignore
                self.tok = AutoTokenizer.from_pretrained(str(snapshot))
                self.kind = f"tokenizer:{snapshot.name}"
            except Exception as exc:  # pragma: no cover
                log.warning("tokenizer unavailable (%s); using chars/3.4", exc)

    def count(self, text: str) -> int:
        if self.tok is not None:
            return len(self.tok(text, add_special_tokens=False)["input_ids"])
        return int(math.ceil(len(text) / 3.4))


# --------------------------------------------------------------------------- schema

TAUCETI_META_DDL = """
CREATE TABLE IF NOT EXISTS tauceti_meta (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    file TEXT,
    line INTEGER,
    is_mathlib INTEGER NOT NULL,
    hash TEXT NOT NULL,
    description_source TEXT,
    description_model TEXT,
    embedding_source TEXT
);
CREATE INDEX IF NOT EXISTS ix_tauceti_meta_hash ON tauceti_meta(hash);
CREATE TABLE IF NOT EXISTS build_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def create_schema(db: Path) -> None:
    """Create LeanExplore's `declarations` table from its own ORM metadata, plus ours."""
    from sqlalchemy import create_engine
    from lean_explore.models import Base  # noqa: F401  (declares Declaration)

    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    engine.dispose()
    con = connect(db)
    con.executescript(TAUCETI_META_DDL)
    con.commit()
    con.close()


# --------------------------------------------------------------------------- adapt

@dataclass
class Row:
    name: str
    kind: str
    module: str
    file: str
    line: int
    signature: str
    docstring: str | None
    is_mathlib: bool
    hash: str
    deps: list[str] | None
    type_full: str = ""   # S0 amendment: real ppExpr type at width 120; preferred over signature


def read_decls(path: Path, limit: int | None, names: set[str] | None, skip_kinds: frozenset[str] = frozenset()) -> list[Row]:
    rows: list[Row] = []
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh):
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("kind") in skip_kinds:
                continue
            if names is not None and d["name"] not in names:
                continue
            deps = d.get("deps") or d.get("dependencies")
            rows.append(Row(
                name=d["name"], kind=d.get("kind") or "def", module=d["module"],
                file=d.get("file") or "", line=int(d.get("line") or 0),
                signature=d.get("signature") or "", docstring=d.get("docstring"),
                is_mathlib=bool(d.get("is_mathlib")), hash=d.get("hash") or "",
                deps=list(deps) if deps else None, type_full=d.get("type_full") or "",
            ))
            if limit and len(rows) >= limit:
                break
    return rows


def source_text_for(row: Row) -> str:
    """LeanExplore's `source_text` is the declaration source; we store the signature.

    If S0's signature already starts with the Lean keyword (e.g. "theorem foo ..."), it is
    used verbatim; otherwise it is wrapped as "<kind> <name> <signature>" so the prompt's
    "Formal Statement" reads like Lean."""
    if row.type_full.strip():  # contract amendment: type_full wins when present
        return f"{row.kind} {row.name} : {row.type_full.strip()}"
    sig = row.signature.strip()
    head = re.sub(r"\A(?:@\[[^\]]*\]\s*)+", "", sig)
    head = re.sub(r"\A(?:(?:private|protected|noncomputable|nonrec|scoped|local|partial|unsafe)\s+)+", "", head)
    first = head.split(None, 1)
    if first and first[0] in LEAN_KEYWORDS or sig.startswith("@["):
        return sig
    sep = " " if sig.startswith(("(", "{", "[", "⦃", ":")) else " : "
    return f"{row.kind} {row.name}{sep}{sig}" if sig else f"{row.kind} {row.name}"


def source_link_for(row: Row, tauceti_sha: str, mathlib_rev: str, tauceti_url: str) -> str:
    if row.is_mathlib or row.module.split(".")[0] in {"Mathlib", "Batteries", "Init", "Lean", "Std", "Lake", "Aesop", "Qq", "Plausible", "ProofWidgets", "ImportGraph", "LeanSearchClient"}:
        base, ref = MATHLIB_REPO_URL, mathlib_rev
    else:
        base, ref = tauceti_url, tauceti_sha
    file = row.file or (row.module.replace(".", "/") + ".lean")
    frag = f"#L{row.line}-L{row.line}" if row.line else ""
    return f"{base}/blob/{ref}/{file}{frag}"


def infer_dependencies(rows: list[Row]) -> dict[str, list[str] | None]:
    """Approximate doc-gen4's dependency list: names referenced in the signature.

    doc-gen4 records the constants appearing in the pretty-printed type; S0's schema has
    no such field yet, so we match identifiers in the signature text against the names
    in the extraction, resolving namespace-relative references the way Lean's pretty
    printer abbreviates them (own namespace prefixes, then the global name)."""
    names = {r.name for r in rows}
    by_suffix: dict[str, list[str]] = defaultdict(list)
    for n in names:
        parts = n.split(".")
        for i in range(1, len(parts)):
            by_suffix[".".join(parts[i:])].append(n)
    out: dict[str, list[str] | None] = {}
    for r in rows:
        if r.deps is not None:
            out[r.name] = [d for d in r.deps if d != r.name] or None
            continue
        own_ns = r.name.split(".")[:-1]
        found: list[str] = []
        seen: set[str] = set()
        for tok in IDENT_RE.findall(r.type_full or r.signature):
            if tok in LEAN_KEYWORDS or len(tok) < 2:
                continue
            cands: list[str] = []
            if tok in names:
                cands = [tok]
            else:
                for k in range(len(own_ns), 0, -1):
                    q = ".".join(own_ns[:k] + [tok])
                    if q in names:
                        cands = [q]
                        break
                if not cands and "." in tok and len(by_suffix.get(tok, [])) == 1:
                    cands = by_suffix[tok]
            for c in cands:
                if c != r.name and c not in seen:
                    seen.add(c)
                    found.append(c)
        out[r.name] = found or None
    return out


def stage_adapt(a: argparse.Namespace, out_dir: Path) -> None:
    decls = a.decls or (a.index_root / a.pin / "decls.jsonl")
    if not decls.exists():
        sys.exit(f"decls.jsonl not found: {decls} (S0 output missing?)")
    s0_manifest = {}
    mpath = decls.parent / "MANIFEST.json"
    if mpath.exists():
        s0_manifest = json.loads(mpath.read_text())
    tauceti_sha = a.tauceti_sha or s0_manifest.get("tauceti_sha") or a.pin.split("-", 1)[-1]
    mathlib_rev = a.mathlib_rev or s0_manifest.get("mathlib_rev") or "master"

    names = set(Path(a.names).read_text().split()) if a.names else None
    rows = read_decls(decls, a.limit, names, frozenset(k for k in a.skip_kinds.split(",") if k))
    if not rows:
        sys.exit("no rows read from decls.jsonl")
    log.info("read %d rows from %s", len(rows), decls)
    deps = infer_dependencies(rows)
    n_with_deps = sum(1 for v in deps.values() if v)
    log.info("dependencies: %d/%d rows have >=1 in-extraction dependency (%s)",
             n_with_deps, len(rows), "from decls.jsonl" if any(r.deps for r in rows) else "inferred from signatures")

    out_dir.mkdir(parents=True, exist_ok=True)
    db = out_dir / "lean_explore.db"
    if db.exists():
        if not a.force:
            sys.exit(f"{db} exists; pass --force to rebuild the database from decls.jsonl")
        for p in out_dir.glob("lean_explore.db*"):
            p.unlink()
    create_schema(db)
    con = connect(db)
    with con:
        con.executemany(
            "INSERT OR IGNORE INTO declarations (name, module, docstring, source_text, source_link, dependencies) VALUES (?,?,?,?,?,?)",
            ((r.name, r.module, r.docstring, source_text_for(r),
              source_link_for(r, tauceti_sha, mathlib_rev, a.tauceti_repo_url),
              json.dumps(deps[r.name]) if deps[r.name] else None) for r in rows),
        )
        con.executemany(
            "INSERT OR REPLACE INTO tauceti_meta (name, kind, file, line, is_mathlib, hash) VALUES (?,?,?,?,?,?)",
            ((r.name, r.kind, r.file, r.line, int(r.is_mathlib), r.hash) for r in rows),
        )
        con.executemany("INSERT OR REPLACE INTO build_meta (key, value) VALUES (?,?)", [
            ("pin", a.pin), ("tauceti_sha", tauceti_sha), ("mathlib_rev", mathlib_rev),
            ("lean_toolchain", s0_manifest.get("lean_toolchain", "")),
            ("decls_path", str(decls)), ("decls_sha256", sha256_file(decls)),
            ("s0_manifest", json.dumps(s0_manifest)), ("adapted_at", utcnow()),
            ("rows", str(len(rows))), ("limit", str(a.limit or "")),
        ])
    n = con.execute("SELECT count(*) FROM declarations").fetchone()[0]
    dup = len(rows) - n
    con.close()
    log.info("adapt: %d declarations written to %s (%d duplicate names ignored)", n, db, dup)


# --------------------------------------------------------------------------- reuse

def reuse_sources(dirs: list[Path]) -> list[tuple[Path, bool]]:
    out = []
    for d in dirs:
        db = d / "lean_explore.db"
        if not db.exists():
            sys.exit(f"--reuse-from {d}: no lean_explore.db there")
        con = connect(db, readonly=True)
        out.append((db, has_table(con, "tauceti_meta")))
        con.close()
    return out


def apply_reuse(db: Path, sources: list[tuple[Path, bool]], strict: bool, copy_embeddings: bool) -> dict[str, int]:
    """Fill NULL descriptions from other LeanExplore databases.

    Keys, in order of preference:
      1. (name, source_text)  -- LeanExplore's own cross-database cache key (exact).
      2. hash                 -- S0's content hash; only when the source has tauceti_meta
                                 (i.e. it is one of our pins). Exact statement+docstring.
      3. name                 -- for upstream prebuilt data (different source_text
                                 convention). With --reuse-strict the docstrings must
                                 match after whitespace normalisation.
    Only rows that already exist in *this* database are ever touched, so nothing is
    emitted that is not in this pin's extraction."""
    stats: dict[str, int] = defaultdict(int)
    con = connect(db)
    todo = con.execute(
        "SELECT d.id, d.name, d.source_text, d.docstring, m.hash FROM declarations d "
        "JOIN tauceti_meta m ON m.name = d.name WHERE d.informalization IS NULL").fetchall()
    if not todo:
        con.close()
        return {"candidates": 0}
    stats["candidates"] = len(todo)
    for src_db, src_has_meta in sources:
        remaining = [r for r in todo if r["id"] not in stats_ids]
        if not remaining:
            break
        src = connect(src_db, readonly=True)
        tag = f"reuse:{src_db.parent.name}"
        updates: list[tuple] = []
        meta_updates: list[tuple] = []
        # batch lookups
        names = [r["name"] for r in remaining]
        found: dict[str, sqlite3.Row] = {}
        for i in range(0, len(names), 900):
            chunk = names[i:i + 900]
            q = ",".join("?" * len(chunk))
            if src_has_meta:
                rows = src.execute(
                    f"SELECT d.name, d.source_text, d.docstring, d.informalization, d.informalization_embedding, m.hash "
                    f"FROM declarations d JOIN tauceti_meta m ON m.name=d.name "
                    f"WHERE d.informalization IS NOT NULL AND d.name IN ({q})", chunk).fetchall()
            else:
                rows = src.execute(
                    f"SELECT name, source_text, docstring, informalization, informalization_embedding, NULL AS hash "
                    f"FROM declarations WHERE informalization IS NOT NULL AND name IN ({q})", chunk).fetchall()
            for s in rows:
                found[s["name"]] = s
        for r in remaining:
            s = found.get(r["name"])
            if s is None:
                continue
            if s["source_text"] == r["source_text"]:
                how = "name+source_text"
            elif src_has_meta and s["hash"] and s["hash"] == r["hash"]:
                how = "hash"
            elif not src_has_meta:
                if strict and norm_ws(s["docstring"]) != norm_ws(r["docstring"]):
                    stats["skipped_docstring_mismatch"] += 1
                    continue
                how = "name"
            else:
                stats["skipped_hash_mismatch"] += 1
                continue
            emb = s["informalization_embedding"] if copy_embeddings else None
            updates.append((s["informalization"], emb, r["id"]))
            meta_updates.append((f"{tag}:{how}", f"{tag}" if emb is not None else None, r["name"]))
            stats_ids.add(r["id"])
            stats[f"reused:{src_db.parent.name}:{how}"] += 1
            if emb is not None:
                stats[f"embeddings_copied:{src_db.parent.name}"] += 1
        with con:
            con.executemany("UPDATE declarations SET informalization=?, informalization_embedding=COALESCE(?, informalization_embedding) WHERE id=?", updates)
            con.executemany("UPDATE tauceti_meta SET description_source=?, embedding_source=COALESCE(?, embedding_source) WHERE name=?", meta_updates)
        src.close()
    con.close()
    return dict(stats)


stats_ids: set[int] = set()


# --------------------------------------------------------------------------- describe

def build_layers(items: list[dict], deps_of: dict[str, list[str]]) -> list[list[dict]]:
    """Kahn layering (same semantics as lean_explore.extract.informalize)."""
    by_name = {d["name"]: d for d in items}
    graph: dict[str, list[str]] = defaultdict(list)
    indeg = {d["name"]: 0 for d in items}
    for d in items:
        for dep in deps_of.get(d["name"], []):
            if dep in by_name and dep != d["name"]:
                graph[dep].append(d["name"])
                indeg[d["name"]] += 1
    layer = [by_name[n] for n in indeg if indeg[n] == 0]
    layers = []
    while layer:
        layers.append(layer)
        nxt = []
        for d in layer:
            for m in graph[d["name"]]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    nxt.append(by_name[m])
        layer = nxt
    rest = [by_name[n] for n in indeg if indeg[n] > 0]
    if rest:
        log.warning("%d declarations in dependency cycles; appended as a final layer", len(rest))
        layers.append(rest)
    return layers


def dependency_context(deps: list[str], described: dict[str, str]) -> str:
    lines = []
    for dep in deps[:MAX_PROMPT_DEPENDENCIES]:
        desc = described.get(dep)
        if not desc:
            continue
        if len(desc) > MAX_DEPENDENCY_DESCRIPTION_LENGTH:
            desc = desc[:MAX_DEPENDENCY_DESCRIPTION_LENGTH - 3] + "..."
        lines.append(f"- {dep}: {desc}")
    return ("Dependencies:\n" + "\n".join(lines)) if lines else ""


def render_prompt(template: str, d: dict, described: dict[str, str]) -> str:
    return template.format(
        name=d["name"], source_text=d["source_text"],
        docstring=d["docstring"] or "No docstring available",
        dependencies=dependency_context(d["deps"], described),
    )


def model_slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model)


def stage_describe(a: argparse.Namespace, out_dir: Path) -> None:
    db = out_dir / "lean_explore.db"
    if not db.exists():
        sys.exit(f"{db} missing; run --stage adapt first")

    # 1. reuse (free)
    if a.reuse_from:
        stats = apply_reuse(db, reuse_sources(a.reuse_from), a.reuse_strict, copy_embeddings=True)
        log.info("reuse: %s", json.dumps(stats, sort_keys=True))

    # 2. what is left
    con = connect(db)
    only = set(Path(a.names).read_text().split()) if a.names else None
    q = ("SELECT d.id, d.name, d.source_text, d.docstring, d.dependencies, m.is_mathlib, m.kind "
         "FROM declarations d JOIN tauceti_meta m ON m.name=d.name WHERE d.informalization IS NULL ORDER BY d.id")
    todo = [dict(r) for r in con.execute(q)]
    if only is not None:
        todo = [d for d in todo if d["name"] in only]
    if a.only_tauceti:
        todo = [d for d in todo if not d["is_mathlib"]]
    if a.informalize_limit:
        todo = todo[: a.informalize_limit]
    for d in todo:
        d["deps"] = json.loads(d["dependencies"]) if d["dependencies"] else []
    described = {r["name"]: r["informalization"] for r in con.execute(
        "SELECT name, informalization FROM declarations WHERE informalization IS NOT NULL")}
    n_total = con.execute("SELECT count(*) FROM declarations").fetchone()[0]
    con.close()

    template = load_prompt_template()
    layers = build_layers(todo, {d["name"]: d["deps"] for d in todo})

    # 3. estimate (no network)
    snapshot = local_snapshot(EMBEDDING_MODEL, a.embedding_revision)
    tc = TokenCounter(snapshot)
    in_tokens = 0
    sim_described = dict(described)
    avg_desc = int(sum(len(v) for v in described.values()) / len(described)) if described else 280
    todo_names = {x["name"] for x in todo}
    for layer in layers:
        for d in layer:
            # deps not yet described but in this run will be described by an earlier layer:
            # simulate with an average-length placeholder so the estimate is not optimistic.
            fake = {dep: ("x" * avg_desc) for dep in d["deps"] if dep in todo_names and dep not in sim_described}
            in_tokens += tc.count(render_prompt(template, d, collections.ChainMap(fake, sim_described)))
        for d in layer:
            sim_described[d["name"]] = "x" * avg_desc
    out_per = a.est_output_tokens
    out_tokens = out_per * len(todo)
    prices = load_prices(a.prices)
    p = prices.get(a.informalize_model)
    est = None
    if p:
        est = in_tokens / 1e6 * p["input_per_m"] + out_tokens / 1e6 * p["output_per_m"]
    n_tc = sum(1 for d in todo if not d["is_mathlib"])
    print("=" * 78)
    print(f"describe estimate for pin {a.pin}  (model {a.informalize_model}; token counter {tc.kind})")
    print(f"  declarations in db            : {n_total}")
    print(f"  already described             : {len(described)}")
    print(f"  to describe this run          : {len(todo)}  (tauceti {n_tc}, mathlib {len(todo) - n_tc}); layers {len(layers)}")
    print(f"  est. input tokens             : {in_tokens:,}  ({in_tokens // max(1, len(todo)):,} per decl)")
    print(f"  est. output tokens            : {out_tokens:,}  ({out_per} per decl)")
    if p:
        print(f"  price $/M in,out              : {p['input_per_m']}, {p['output_per_m']}  (from {a.prices.name}, {p.get('as_of', '?')})")
        print(f"  est. cost                     : ${est:.4f}   (x1.5 margin: ${est * 1.5:.4f})")
    else:
        print(f"  price                         : unknown model {a.informalize_model!r}; add it to {a.prices}")
    print("=" * 78)
    if not todo:
        print("nothing to describe")
        return
    if a.dry_run:
        print("--dry-run: no API calls made")
        return
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key or not a.i_approve_spend:
        missing = [] if key else ["OPENROUTER_API_KEY"]
        if not a.i_approve_spend:
            missing.append("--i-approve-spend")
        sys.exit(f"refusing to spend: missing {', '.join(missing)}")
    if a.max_spend is not None and p is None:
        sys.exit(f"refusing: --max-spend needs a known price for {a.informalize_model!r} to track actual spend")
    if a.max_spend is not None and est is not None and est * 1.5 > a.max_spend and not a.no_margin_check:
        sys.exit(f"refusing: estimate with margin ${est * 1.5:.4f} exceeds --max-spend {a.max_spend}")

    # 4. run
    asyncio.run(run_describe(a, db, layers, described, template, key, p))


class StopRun(Exception):
    pass


def _is_credit_error(exc: Exception) -> bool:
    code = getattr(exc, "status_code", None)
    msg = str(exc).lower()
    return code in (401, 402, 403) or "insufficient" in msg or "credits" in msg or "payment" in msg


async def openai_caller(a, key: str):
    """Default describer: one OpenAI-compatible chat call. Returns (text, usage dict, finish_reason)."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(base_url=a.base_url, api_key=key, timeout=a.timeout, max_retries=0)

    async def call(prompt: str):
        resp = await client.chat.completions.create(
            model=a.informalize_model, messages=[{"role": "user", "content": prompt}],
            temperature=DESCRIBE_TEMPERATURE, max_tokens=a.max_tokens)
        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        usage = getattr(resp, "usage", None)
        return (text, {"prompt_tokens": getattr(usage, "prompt_tokens", None),
                       "completion_tokens": getattr(usage, "completion_tokens", None)},
                resp.choices[0].finish_reason if resp.choices else None)
    return call


async def run_describe(a, db: Path, layers, described: dict[str, str], template: str, key: str, price,
                       caller=None) -> None:
    """Describe layer by layer; every completed call is persisted (row + log line) as it returns.

    Spend is tracked per completed call from the returned usage; when it reaches --max-spend, or the
    provider reports insufficient credits / auth failure, no new call starts, in-flight calls finish and
    are persisted, and the process exits non-zero. Rerun semantics: todo = rows with NULL informalization.
    """
    if caller is None:
        caller = await openai_caller(a, key)
    nconc = max(1, a.informalize_max_concurrent)
    commit_every = max(1, a.commit_every)
    con = connect(db)
    logf = (db.parent / "describe-log.jsonl").open("a", encoding="utf-8")
    tag = f"llm:{a.informalize_model}"
    spent = {"in": 0, "out": 0, "ok": 0, "fail": 0, "usd": 0.0}
    started = time.time()
    state = {"stop": None, "pending": 0}
    # --max-spend is a cumulative cap: tokens already paid for in earlier invocations (describe-log.jsonl) count toward it
    logp = db.parent / "describe-log.jsonl"
    if logp.exists() and price:
        for line in logp.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("prompt_tokens"):
                spent["in"] += rec["prompt_tokens"]
                spent["out"] += rec.get("completion_tokens") or 0
        spent["usd"] = spent["in"] / 1e6 * price["input_per_m"] + spent["out"] / 1e6 * price["output_per_m"]
        log.info("prior spend from describe-log.jsonl: in=%d out=%d usd=%.4f (counts toward --max-spend)", spent["in"], spent["out"], spent["usd"])

    def persist(d: dict, text: str | None, info: dict) -> None:
        logf.write(json.dumps(info, ensure_ascii=False) + "\n")
        logf.flush()
        if info.get("prompt_tokens"):
            spent["in"] += info["prompt_tokens"]
            spent["out"] += info.get("completion_tokens") or 0
            if price:
                spent["usd"] = spent["in"] / 1e6 * price["input_per_m"] + spent["out"] / 1e6 * price["output_per_m"]
        if text:
            spent["ok"] += 1
            described[d["name"]] = text
            con.execute("UPDATE declarations SET informalization=? WHERE id=?", (text, d["id"]))
            con.execute("UPDATE tauceti_meta SET description_source=?, description_model=? WHERE name=?",
                        (tag, a.informalize_model, d["name"]))
            state["pending"] += 1
            if state["pending"] >= commit_every:
                con.commit()
                state["pending"] = 0
        else:
            spent["fail"] += 1
        if a.max_spend is not None and spent["usd"] >= a.max_spend and not state["stop"]:
            state["stop"] = f"actual spend ${spent['usd']:.4f} reached --max-spend {a.max_spend}"
        done = spent["ok"] + spent["fail"]
        if done % 500 == 0:
            log.info("progress: ok=%d fail=%d tokens in=%d out=%d usd=%.4f elapsed=%ds",
                     spent["ok"], spent["fail"], spent["in"], spent["out"], spent["usd"], time.time() - started)

    async def one(d: dict) -> None:
        prompt = render_prompt(template, d, described)
        last_exc: Exception | None = None
        for attempt in range(1, 4):
            if state["stop"]:
                return
            t0 = time.time()
            try:
                text, usage, finish = await caller(prompt)
                info = {"name": d["name"], "model": a.informalize_model, "attempt": attempt,
                        "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                        "ms": int((time.time() - t0) * 1000), "at": utcnow(),
                        "finish_reason": finish, "ok": bool(text)}
                persist(d, text or None, info)
                return
            except Exception as exc:  # network / rate limit / provider error
                last_exc = exc
                if _is_credit_error(exc):
                    state["stop"] = f"provider refused (credits/auth): {exc!r}"[:300]
                    return
                await asyncio.sleep(min(10, 2 ** attempt))
        persist(d, None, {"name": d["name"], "model": a.informalize_model, "ok": False,
                          "error": repr(last_exc)[:300], "at": utcnow()})

    async def worker(q: "asyncio.Queue[dict]") -> None:
        while not state["stop"]:
            try:
                d = q.get_nowait()
            except asyncio.QueueEmpty:
                return
            await one(d)

    try:
        for li, layer in enumerate(layers, 1):
            if state["stop"]:
                break
            log.info("layer %d/%d: %d declarations", li, len(layers), len(layer))
            q: asyncio.Queue = asyncio.Queue()
            for d in layer:
                q.put_nowait(d)
            await asyncio.gather(*(worker(q) for _ in range(min(nconc, len(layer)))))
            con.commit()
            state["pending"] = 0
            log.info("layer %d done: ok=%d fail=%d usd=%.4f", li, spent["ok"], spent["fail"], spent["usd"])
    finally:
        try:
            con.commit()
        finally:
            logf.write(json.dumps({"summary": spent, "model": a.informalize_model, "stop": state["stop"], "at": utcnow()}) + "\n")
            logf.close()
            con.close()
    print(json.dumps({"describe_summary": spent, "stop": state["stop"]}, indent=2))
    if state["stop"]:
        log.error("stopped: %s (rerun the same command to resume)", state["stop"])
        sys.exit(3)


# --------------------------------------------------------------------------- embed

def local_snapshot(repo: str, revision: str) -> Path | None:
    """Path of an already-downloaded HF snapshot (no network)."""
    try:
        from huggingface_hub import snapshot_download
        return Path(snapshot_download(repo, revision=revision, local_files_only=True))
    except Exception:
        return None


def ensure_snapshot(repo: str, revision: str) -> Path:
    from huggingface_hub import snapshot_download
    return Path(snapshot_download(repo, revision=revision))


def stage_embed(a: argparse.Namespace, out_dir: Path) -> None:
    db = out_dir / "lean_explore.db"
    if not db.exists():
        sys.exit(f"{db} missing; run --stage adapt first")
    if a.reuse_from:
        stats = apply_reuse(db, reuse_sources(a.reuse_from), a.reuse_strict, copy_embeddings=True)
        log.info("reuse (embed stage): %s", json.dumps(stats, sort_keys=True))
        reuse_embeddings_by_text(db, a.reuse_from)

    con = connect(db)
    todo = [dict(r) for r in con.execute(
        "SELECT d.id, d.name, d.informalization, d.docstring, d.source_text FROM declarations d "
        "WHERE d.informalization_embedding IS NULL ORDER BY d.id")]
    with_desc = [d for d in todo if d["informalization"]]
    without = [d for d in todo if not d["informalization"]]
    log.info("embed: %d rows need embeddings (%d with descriptions, %d without)", len(todo), len(with_desc), len(without))
    texts: list[tuple[int, str, str]] = [(d["id"], d["informalization"], "description") for d in with_desc]
    if a.embed_fallback_text:
        for d in without:
            texts.append((d["id"], f"{d['name']}. {norm_ws(d['docstring'])} {norm_ws(d['source_text'])}".strip(), "fallback-text"))
    if a.embedding_limit:
        texts = texts[: a.embedding_limit]
    if not texts:
        con.close()
        log.info("embed: nothing to do")
        return

    snap = ensure_snapshot(a.embedding_model, a.embedding_revision)
    import numpy as np
    import torch
    torch.manual_seed(0)
    if a.threads:
        torch.set_num_threads(a.threads)
    from lean_explore.util.embedding_client import EmbeddingClient
    client = EmbeddingClient(model_name=str(snap), device=a.device, max_length=a.embedding_max_seq_length, batch_size=a.embedding_batch_size)
    src_tag = f"local:{a.embedding_model}@{a.embedding_revision[:12]}"
    t0 = time.time()
    done = 0
    for i in range(0, len(texts), a.embedding_batch_size):
        batch = texts[i:i + a.embedding_batch_size]
        resp = asyncio.run(client.embed([t for _, t, _ in batch], is_query=False))
        vecs = np.asarray(resp.embeddings, dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = vecs / np.maximum(norms, 1e-12)
        with con:
            con.executemany("UPDATE declarations SET informalization_embedding=? WHERE id=?",
                            [(struct.pack(f"{v.size}f", *v.tolist()), bid) for (bid, _, _), v in zip(batch, vecs)])
            con.executemany("UPDATE tauceti_meta SET embedding_source=? WHERE name=(SELECT name FROM declarations WHERE id=?)",
                            [(f"{src_tag}:{kind}", bid) for (bid, _, kind) in batch])
        done += len(batch)
        if done % (a.embedding_batch_size * 10) == 0 or done == len(texts):
            log.info("embedded %d/%d (%.1f/s)", done, len(texts), done / max(1e-6, time.time() - t0))
    with con:
        con.executemany("INSERT OR REPLACE INTO build_meta (key, value) VALUES (?,?)", [
            ("embedding_model", a.embedding_model), ("embedding_revision", a.embedding_revision),
            ("embedding_dim", str(vecs.shape[1])), ("embedding_max_seq_length", str(a.embedding_max_seq_length)),
            ("embedded_at", utcnow()),
        ])
    con.close()


def reuse_embeddings_by_text(db: Path, dirs: list[Path]) -> None:
    """LeanExplore's own embedding cache rule: identical informalization text -> same vector."""
    con = connect(db)
    todo = con.execute("SELECT id, informalization FROM declarations WHERE informalization IS NOT NULL AND informalization_embedding IS NULL").fetchall()
    if not todo:
        con.close()
        return
    by_text = {r["informalization"]: r["id"] for r in todo}
    hits = 0
    for d in dirs:
        src = connect(d / "lean_explore.db", readonly=True)
        texts = list(by_text)
        ups = []
        for i in range(0, len(texts), 500):
            chunk = texts[i:i + 500]
            q = ",".join("?" * len(chunk))
            for s in src.execute(f"SELECT informalization, informalization_embedding FROM declarations WHERE informalization_embedding IS NOT NULL AND informalization IN ({q})", chunk):
                rid = by_text.pop(s["informalization"], None)
                if rid is not None:
                    ups.append((s["informalization_embedding"], f"reuse:{d.name}", rid))
        with con:
            con.executemany("UPDATE declarations SET informalization_embedding=? WHERE id=?", [(e, rid) for e, _, rid in ups])
            con.executemany("UPDATE tauceti_meta SET embedding_source=? WHERE name=(SELECT name FROM declarations WHERE id=?)", [(t, rid) for _, t, rid in ups])
        hits += len(ups)
        src.close()
    con.close()
    log.info("embedding reuse by identical text: %d", hits)


# --------------------------------------------------------------------------- index

def stage_index(a: argparse.Namespace, out_dir: Path) -> None:
    db = out_dir / "lean_explore.db"
    if not db.exists():
        sys.exit(f"{db} missing; run --stage adapt first")
    import faiss
    import numpy as np
    con = connect(db, readonly=True)
    rows = con.execute("SELECT id, informalization_embedding FROM declarations WHERE informalization_embedding IS NOT NULL ORDER BY id").fetchall()
    con.close()
    if not rows:
        sys.exit("index: no embeddings in the database; run --stage embed first")
    ids = [r["id"] for r in rows]
    dim = len(rows[0]["informalization_embedding"]) // 4
    X = np.empty((len(rows), dim), dtype=np.float32)
    for i, r in enumerate(rows):
        X[i] = np.frombuffer(r["informalization_embedding"], dtype=np.float32)
    faiss.normalize_L2(X)
    n = len(ids)
    if n < FAISS_FLAT_BELOW:
        index = faiss.IndexFlatIP(dim)
        index.add(X)
        kind = {"type": "IndexFlatIP", "nlist": None}
    else:
        nlist = max(256, int(math.sqrt(n)))  # same rule as lean_explore.extract.index
        quant = faiss.IndexFlatIP(dim)
        index = faiss.IndexIVFFlat(quant, dim, nlist, faiss.METRIC_INNER_PRODUCT)
        index.train(X)
        index.add(X)
        kind = {"type": "IndexIVFFlat", "nlist": nlist, "nprobe_at_query": 64}
    faiss.write_index(index, str(out_dir / "informalization_faiss.index"))
    (out_dir / "informalization_faiss_ids_map.json").write_text(json.dumps(ids))
    log.info("faiss: %s over %d vectors (dim %d)", kind["type"], n, dim)

    from sqlalchemy.ext.asyncio import create_async_engine
    from lean_explore.extract.index import build_bm25_indices
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    asyncio.run(build_bm25_indices(engine, output_directory=out_dir))
    asyncio.run(engine.dispose())
    write_manifest(a, out_dir, faiss_info=kind)


def write_manifest(a: argparse.Namespace, out_dir: Path, faiss_info: dict | None = None) -> None:
    db = out_dir / "lean_explore.db"
    con = connect(db, readonly=True)
    bm = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM build_meta")}
    counts = {
        "declarations": con.execute("SELECT count(*) FROM declarations").fetchone()[0],
        "tauceti": con.execute("SELECT count(*) FROM tauceti_meta WHERE is_mathlib=0").fetchone()[0],
        "mathlib": con.execute("SELECT count(*) FROM tauceti_meta WHERE is_mathlib=1").fetchone()[0],
        "described": con.execute("SELECT count(*) FROM declarations WHERE informalization IS NOT NULL").fetchone()[0],
        "embedded": con.execute("SELECT count(*) FROM declarations WHERE informalization_embedding IS NOT NULL").fetchone()[0],
        "described_by_source": {r[0] or "none": r[1] for r in con.execute(
            "SELECT m.description_source, count(*) FROM tauceti_meta m JOIN declarations d ON d.name=m.name WHERE d.informalization IS NOT NULL GROUP BY 1")},
        "embedded_by_source": {r[0] or "none": r[1] for r in con.execute(
            "SELECT m.embedding_source, count(*) FROM tauceti_meta m JOIN declarations d ON d.name=m.name WHERE d.informalization_embedding IS NOT NULL GROUP BY 1")},
        "by_kind": {r[0]: r[1] for r in con.execute("SELECT kind, count(*) FROM tauceti_meta GROUP BY kind")},
    }
    counts["null_description"] = counts["declarations"] - counts["described"]
    counts["null_embedding"] = counts["declarations"] - counts["embedded"]
    describer_models = [r[0] for r in con.execute("SELECT DISTINCT description_model FROM tauceti_meta WHERE description_model IS NOT NULL")]
    con.close()
    files = ["lean_explore.db", "informalization_faiss.index", "informalization_faiss_ids_map.json", "bm25_ids_map.json"]
    files += [f"bm25_name_raw/{p.name}" for p in sorted((out_dir / "bm25_name_raw").glob("*"))] if (out_dir / "bm25_name_raw").exists() else []
    files += [f"bm25_name_spaced/{p.name}" for p in sorted((out_dir / "bm25_name_spaced").glob("*"))] if (out_dir / "bm25_name_spaced").exists() else []
    sha = {f: sha256_file(out_dir / f) for f in files if (out_dir / f).exists()}
    manifest = {
        "tool": "explore",
        "pin": a.pin,
        # contract amendment: the five identity fields at top level, copied from S0's MANIFEST.json
        "tauceti_sha": bm.get("tauceti_sha"), "mathlib_rev": bm.get("mathlib_rev"),
        "lean_toolchain": bm.get("lean_toolchain"), "decls_sha256": bm.get("decls_sha256"),
        "built_at": utcnow(),
        "route": "adapter (decls.jsonl -> LeanExplore SQLite schema; parse stage bypassed)",
        "lean_explore": {"version": importlib.metadata.version("lean-explore"), "source": "PyPI"},
        "python": sys.version.split()[0],
        "source": {
            "tauceti_sha": bm.get("tauceti_sha"), "mathlib_rev": bm.get("mathlib_rev"),
            "lean_toolchain": bm.get("lean_toolchain"), "decls_path": bm.get("decls_path"),
            "decls_sha256": bm.get("decls_sha256"), "limit": bm.get("limit") or None,
            "s0_manifest": json.loads(bm.get("s0_manifest") or "{}"),
        },
        "models": {
            "embedding": {"id": bm.get("embedding_model", a.embedding_model), "revision": bm.get("embedding_revision", a.embedding_revision),
                          "dim": int(bm["embedding_dim"]) if bm.get("embedding_dim") else None,
                          "max_seq_length": int(bm.get("embedding_max_seq_length", a.embedding_max_seq_length))},
            "reranker": {"id": a.reranker_model, "revision": a.reranker_revision, "max_length": 256,
                         "runs": "local (CPU/GPU in the service process); deterministic for a fixed index"},
            "describers": describer_models,
            "describe_prompt_sha256": hashlib.sha256(load_prompt_template().encode()).hexdigest(),
        },
        "search_defaults": {"rerank_top": a.rerank_top, "faiss_k": 1000, "bm25_k": 1000},
        "faiss": faiss_info,
        "counts": counts,
        "sha256": sha,
    }
    (out_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    copy_dir = HERE / "manifests"
    copy_dir.mkdir(exist_ok=True)
    (copy_dir / f"{a.pin}.json").write_text(json.dumps(manifest, indent=2) + "\n")
    log.info("manifest written: %s (copy in %s)", out_dir / "MANIFEST.json", copy_dir)


# --------------------------------------------------------------------------- export

def stage_export(a: argparse.Namespace, out_dir: Path) -> None:
    """Write (name, kind, signature, docstring, description, model) for scoring."""
    import csv
    db = out_dir / "lean_explore.db"
    con = connect(db, readonly=True)
    only = set(Path(a.names).read_text().split()) if a.names else None
    rows = con.execute("SELECT d.name, m.kind, m.is_mathlib, d.module, d.source_text, d.docstring, d.informalization, m.description_source, m.description_model "
                       "FROM declarations d JOIN tauceti_meta m ON m.name=d.name ORDER BY d.id").fetchall()
    out = Path(a.export_to) if a.export_to else out_dir / "descriptions.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "kind", "is_mathlib", "module", "signature", "docstring", "description", "description_source", "model", "verdict(right/vague/wrong)", "notes"])
        n = 0
        for r in rows:
            if only is not None and r["name"] not in only:
                continue
            w.writerow([r["name"], r["kind"], r["is_mathlib"], r["module"], r["source_text"], r["docstring"] or "", r["informalization"] or "", r["description_source"] or "", r["description_model"] or "", "", ""])
            n += 1
    con.close()
    print(f"wrote {n} rows to {out}")


# --------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pin", help="e.g. b3-11bc13335fb9")
    ap.add_argument("--stage", choices=["adapt", "describe", "embed", "index", "export", "all"], required=True)
    ap.add_argument("--index-root", type=Path, default=DEFAULT_INDEX_ROOT)
    ap.add_argument("--explore-root", type=Path, default=DEFAULT_EXPLORE_ROOT)
    ap.add_argument("--decls", type=Path, help="override <index-root>/<pin>/decls.jsonl")
    ap.add_argument("--out-dir", type=Path, help="override <explore-root>/<pin>")
    ap.add_argument("--limit", type=int, help="adapt only the first N rows (CPU slice)")
    ap.add_argument("--names", type=Path, help="file of declaration names: adapt/describe/export only these")
    ap.add_argument("--force", action="store_true", help="adapt: overwrite an existing lean_explore.db")
    ap.add_argument("--tauceti-sha"); ap.add_argument("--mathlib-rev")
    ap.add_argument("--tauceti-repo-url", default=TAUCETI_REPO_URL)
    # describe
    ap.add_argument("--informalize-model", default=DEFAULT_DESCRIBER)
    ap.add_argument("--informalize-limit", type=int)
    ap.add_argument("--informalize-max-concurrent", type=int, default=8)
    ap.add_argument("--skip-kinds", default="ctor", help="adapt: comma list of decls.jsonl kinds to drop (default: ctor, like LeanExplore dropping .mk)")
    ap.add_argument("--only-tauceti", action="store_true", help="describe: never spend on Mathlib rows")
    ap.add_argument("--reuse-from", type=Path, action="append", default=[], help="dir with lean_explore.db (another pin or the upstream dir); repeatable")
    ap.add_argument("--reuse-strict", action="store_true", help="name-keyed reuse requires equal docstrings")
    ap.add_argument("--dry-run", action="store_true", help="describe: estimate only, no network")
    ap.add_argument("--i-approve-spend", action="store_true")
    ap.add_argument("--max-spend", type=float, help="USD ceiling (estimate x1.5 before unless --no-margin-check; actual per-call spend during)")
    ap.add_argument("--no-margin-check", action="store_true",
                    help="skip the estimate x1.5 <= --max-spend start-up check; only together with per-row spend tracking "
                         "(actual spend is tracked per completed call and the run stops at --max-spend)")
    ap.add_argument("--commit-every", type=int, default=100, help="describe: SQLite commit every N persisted rows (and at layer end)")
    ap.add_argument("--base-url", default=os.environ.get("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL), help="OpenAI-compatible endpoint (vLLM works: set this and any OPENROUTER_API_KEY)")
    ap.add_argument("--prices", type=Path, default=HERE / "models.json")
    ap.add_argument("--est-output-tokens", type=int, default=110, help="per-declaration output estimate (upstream mean description is 283 chars)")
    ap.add_argument("--max-tokens", type=int, default=600)
    ap.add_argument("--timeout", type=float, default=120.0)
    # embed
    ap.add_argument("--embedding-model", default=EMBEDDING_MODEL)
    ap.add_argument("--embedding-revision", default=EMBEDDING_REVISION)
    ap.add_argument("--embedding-batch-size", type=int, default=32)
    ap.add_argument("--embedding-max-seq-length", type=int, default=512)
    ap.add_argument("--embedding-limit", type=int)
    ap.add_argument("--embed-fallback-text", action="store_true",
                    help="rows without a description get an embedding of 'name. docstring signature' (description stays empty); off by default")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--threads", type=int, default=0)
    # service-facing manifest fields
    ap.add_argument("--reranker-model", default=RERANKER_MODEL)
    ap.add_argument("--reranker-revision", default=RERANKER_REVISION)
    ap.add_argument("--rerank-top", type=int, default=25)
    ap.add_argument("--export-to", help="export: output CSV path")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "sentence_transformers", "transformers", "huggingface_hub", "urllib3", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    out_dir = a.out_dir or (a.explore_root / a.pin)
    stages = ["adapt", "describe", "embed", "index"] if a.stage == "all" else [a.stage]
    for s in stages:
        log.info("== stage %s (pin %s, out %s)", s, a.pin, out_dir)
        {"adapt": stage_adapt, "describe": stage_describe, "embed": stage_embed,
         "index": stage_index, "export": stage_export}[s](a, out_dir)


if __name__ == "__main__":
    main()
