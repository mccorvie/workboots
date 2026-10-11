#!/usr/bin/env python3
"""Convert doc-gen4's `api-docs.db` (SQLite, written by the `docInfo` facets) into `decls.jsonl`.

One JSON object per line, one line per declaration, fields exactly as in workboots-plan.md
("Index artefacts"):

    name, kind, module, file, line, signature, docstring, is_mathlib, hash

* `signature` is doc-gen4's declaration header rendered as plain text, i.e. the text that
  `docInfoHeader` puts in `<div class="decl_header">`: `<kind description> <name> <binders>
  [extends <parents>] : <type>`. Binders and types are doc-gen4's `RenderedCode` blobs (Lean's
  pretty printer output with link tags), flattened by concatenating their text leaves; line breaks
  from the pretty printer are preserved.
* `kind` follows doc-gen4's JSON `getKind` labels (`def`, `theorem`, `instance`, `structure`,
  `class`, `inductive`, `axiom`, `opaque`, `ctor`), except that `class inductive` is kept distinct.
* `file` is the module's source path relative to its package root (`TauCeti/...`, `Mathlib/...`,
  `Init/...`), derived from the module name.
* `line` is the 1-based start line of the declaration (doc-gen4's `declaration_ranges`), or null.
* `docstring` is the Markdown docstring, or "".
* `is_mathlib` is true for `Mathlib.*` modules only (Batteries, Aesop, Lean core, ... are false and
  not TauCeti either; use the module prefix to tell packages apart).
* `hash` is sha256(name + "\\n" + signature + "\\n" + docstring), hex.

Rows: every `name_info` row except private names (`_private.`), de-duplicated by declaration name
(preferring rows doc-gen4 renders, then rows with a docstring, then the lowest position).
Constructors and structure projections (which doc-gen4 does not render as standalone pages,
`render = 0`) ARE kept: they are real declarations a search should find.

Also writes MANIFEST.json next to the output (counts, sha256/size of decls.jsonl, extractor
sha256, plus any `--meta key=value` pairs the caller passes).

Stdlib only. Usage:
    extract.py --db docbuild/.lake/build/api-docs.db --out decls.jsonl [--meta key=value ...]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict

# ----------------------------------------------------------------------------------------------
# RenderedCode blob decoding (leansqlite `ToBinary` format, doc-gen4 `RenderedCode`)
#
#   Nat      : little-endian base-128 varint, continuation bit 0x80
#   String   : Nat byte length, then UTF-8 bytes
#   Name     : tag byte 0 = anonymous | 1 = str (pre : Name) (s : String) | 2 = num (pre : Name) (n : Nat)
#   Tag      : byte 0 keyword | 1 string | 2 const (n : Name) | 3..6 sort | 7 otherExpr
#   TaggedText: byte 0 text (s : String) | 1 tag (t : Tag) (inner) | 2 append (xs : Array TaggedText)
#   Array    : Nat size, then elements
# ----------------------------------------------------------------------------------------------


class BlobError(ValueError):
    pass


def _read_nat(buf: bytes, i: int) -> tuple[int, int]:
    n = 0
    shift = 0
    while True:
        b = buf[i]
        i += 1
        if b >= 0x80:
            n |= (b & 0x7F) << shift
            shift += 7
        else:
            n |= b << shift
            return n, i


def _read_string(buf: bytes, i: int) -> tuple[str, int]:
    size, i = _read_nat(buf, i)
    return buf[i:i + size].decode("utf-8"), i + size


def _skip_name(buf: bytes, i: int) -> int:
    # Name is right-nested: str/num carry their prefix first, so walk until anonymous.
    while True:
        tag = buf[i]
        i += 1
        if tag == 0:
            return i
        if tag == 1:
            # prefix is serialized BEFORE the string component; recurse on prefix then read string
            i = _skip_name(buf, i)
            _, i = _read_string(buf, i)
            return i
        if tag == 2:
            i = _skip_name(buf, i)
            _, i = _read_nat(buf, i)
            return i
        raise BlobError(f"bad Name tag {tag} at {i - 1}")


def _skip_tag(buf: bytes, i: int) -> int:
    t = buf[i]
    i += 1
    if t == 2:
        return _skip_name(buf, i)
    if t in (0, 1, 3, 4, 5, 6, 7):
        return i
    raise BlobError(f"bad RenderedCode.Tag {t} at {i - 1}")


def flatten(blob: bytes | None) -> str:
    """Concatenate the text leaves of a serialized `RenderedCode` (iterative; no recursion limit)."""
    if not blob:
        return ""
    out: list[str] = []
    i = 0
    stack = [1]  # items still to read at each append level
    while stack:
        if stack[-1] == 0:
            stack.pop()
            continue
        stack[-1] -= 1
        b = blob[i]
        i += 1
        if b == 0:
            s, i = _read_string(blob, i)
            out.append(s)
        elif b == 1:
            i = _skip_tag(blob, i)
            stack.append(1)
        elif b == 2:
            n, i = _read_nat(blob, i)
            stack.append(n)
        else:
            raise BlobError(f"bad TaggedText tag {b} at {i - 1}")
    if i != len(blob):
        raise BlobError(f"trailing bytes: consumed {i} of {len(blob)}")
    return "".join(out)


# ----------------------------------------------------------------------------------------------
# Kind labels (mirrors DocGen4.Process.DocInfo.getKind / getKindDescription)
# ----------------------------------------------------------------------------------------------

DB_KIND_TO_KIND = {
    "axiom": "axiom",
    "theorem": "theorem",
    "opaque": "opaque",
    "definition": "def",
    "instance": "instance",
    "inductive": "inductive",
    "structure": "structure",
    "class": "class",
    "class inductive": "class inductive",
    "constructor": "ctor",
}


def kind_description(db_kind: str, extra: dict) -> str:
    """The keyword(s) doc-gen4 prints before the name (`noncomputable def`, `abbrev`, ...)."""
    if db_kind == "axiom":
        return "unsafe axiom" if extra.get("axiom_unsafe") else "axiom"
    if db_kind == "theorem":
        return "theorem"
    if db_kind == "opaque":
        return {"safe": "opaque", "unsafe": "unsafe opaque", "partial": "partial def"}.get(
            extra.get("opaque_safety", "safe"), "opaque")
    if db_kind in ("definition", "instance"):
        mods = []
        if extra.get("def_unsafe"):
            mods.append("unsafe")
        if extra.get("def_noncomputable"):
            mods.append("noncomputable")
        if db_kind == "instance":
            mods.append("instance")
        else:
            mods.append("abbrev" if extra.get("def_hints") == "abbrev" else "def")
        return " ".join(mods)
    if db_kind == "inductive":
        return "unsafe inductive" if extra.get("inductive_unsafe") else "inductive"
    if db_kind == "class inductive":
        return "class inductive"
    if db_kind == "structure":
        return "structure"
    if db_kind == "class":
        return "class"
    if db_kind == "constructor":
        return "constructor"
    return db_kind


def module_file(module: str) -> str:
    return module.replace(".", "/") + ".lean"


def top_package(module: str) -> str:
    return module.split(".", 1)[0]


# ----------------------------------------------------------------------------------------------


def load(db_path: str):
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = None
    cur = con.cursor()

    def table_exists(name: str) -> bool:
        return cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    for t in ("name_info", "declaration_args", "declaration_ranges", "declaration_markdown_docstrings"):
        if not table_exists(t):
            raise SystemExit(f"{db_path}: missing table {t}; is this a doc-gen4 api-docs.db?")

    args: dict[tuple, list[bytes]] = defaultdict(list)
    for mod, pos, seq, binder in cur.execute(
            "SELECT module_name, position, sequence, binder FROM declaration_args ORDER BY module_name, position, sequence"):
        args[(mod, pos)].append(binder)

    extras: dict[tuple, dict] = defaultdict(dict)
    if table_exists("definitions"):
        for mod, pos, unsafe, hints, noncomp in cur.execute(
                "SELECT module_name, position, is_unsafe, hints, is_noncomputable FROM definitions"):
            extras[(mod, pos)].update(def_unsafe=bool(unsafe), def_hints=hints if isinstance(hints, str) else "regular",
                                      def_noncomputable=bool(noncomp))
    if table_exists("opaques"):
        for mod, pos, safety in cur.execute("SELECT module_name, position, safety FROM opaques"):
            extras[(mod, pos)]["opaque_safety"] = safety
    if table_exists("axioms"):
        for mod, pos, unsafe in cur.execute("SELECT module_name, position, is_unsafe FROM axioms"):
            extras[(mod, pos)]["axiom_unsafe"] = bool(unsafe)
    if table_exists("inductives"):
        for mod, pos, unsafe in cur.execute("SELECT module_name, position, is_unsafe FROM inductives"):
            extras[(mod, pos)]["inductive_unsafe"] = bool(unsafe)
    parents: dict[tuple, list[bytes]] = defaultdict(list)
    if table_exists("structure_parents"):
        for mod, pos, seq, ptype in cur.execute(
                "SELECT module_name, position, sequence, type FROM structure_parents ORDER BY module_name, position, sequence"):
            parents[(mod, pos)].append(ptype)

    rows = cur.execute(
        "SELECT n.module_name, n.position, n.kind, n.name, n.type, n.sorried, n.render, r.start_line, d.text "
        "FROM name_info n "
        "LEFT JOIN declaration_ranges r ON r.module_name = n.module_name AND r.position = n.position "
        "LEFT JOIN declaration_markdown_docstrings d ON d.module_name = n.module_name AND d.position = n.position "
        "ORDER BY n.module_name, n.position").fetchall()
    modules = [m for (m,) in cur.execute("SELECT name FROM modules ORDER BY name")]
    con.close()
    return rows, args, extras, parents, modules


def build_records(rows, args, extras, parents, warn):
    # Pick one row per declaration name.
    best: dict[str, tuple] = {}
    for row in rows:
        mod, pos, kind, name, type_blob, sorried, render, line, doc = row
        if name.startswith("_private.") or "._private." in name:
            continue
        rank = (1 if render else 0, 1 if doc else 0, -pos)
        prev = best.get(name)
        if prev is None or rank > prev[0]:
            best[name] = (rank, row)
    records = []
    bad_blobs = 0
    for name, (_, row) in best.items():
        mod, pos, kind, _, type_blob, sorried, render, line, doc = row
        key = (mod, pos)
        try:
            binders = [flatten(b) for b in args.get(key, ())]
            type_text = flatten(type_blob)
            parent_texts = [flatten(p) for p in parents.get(key, ())]
        except (BlobError, IndexError, UnicodeDecodeError) as e:
            bad_blobs += 1
            if bad_blobs <= 5:
                warn(f"blob decode failed for {name} in {mod}: {e}")
            binders, type_text, parent_texts = [], "<undecodable>", []
        parts = [kind_description(kind or "", extras.get(key, {})), name]
        parts.extend(binders)
        if parent_texts:
            parts.append("extends " + ", ".join(parent_texts))
        signature = " ".join(parts) + " : " + type_text
        docstring = doc or ""
        h = hashlib.sha256((name + "\n" + signature + "\n" + docstring).encode("utf-8")).hexdigest()
        records.append({
            "name": name,
            "kind": DB_KIND_TO_KIND.get(kind or "", kind or ""),
            "module": mod,
            "file": module_file(mod),
            "line": line,
            "signature": signature,
            "docstring": docstring,
            "is_mathlib": mod == "Mathlib" or mod.startswith("Mathlib."),
            "hash": h,
        })
    records.sort(key=lambda r: (r["module"], r["line"] if r["line"] is not None else 1 << 30, r["name"]))
    return records, bad_blobs


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="path to api-docs.db")
    ap.add_argument("--out", required=True, help="path of decls.jsonl to write")
    ap.add_argument("--manifest", help="path of MANIFEST.json (default: next to --out)")
    ap.add_argument("--meta", action="append", default=[], metavar="KEY=VALUE",
                    help="extra MANIFEST fields (batch, pin, tauceti_sha, ...); repeatable")
    ap.add_argument("--only-prefix", action="append", default=[], metavar="PREFIX",
                    help="keep only modules with this prefix (smoke tests); repeatable")
    a = ap.parse_args()

    def warn(msg: str) -> None:
        print(f"extract.py: warning: {msg}", file=sys.stderr)

    rows, args, extras, parents, modules = load(a.db)
    if a.only_prefix:
        pfx = tuple(a.only_prefix)
        rows = [r for r in rows if r[0].startswith(pfx)]
        modules = [m for m in modules if m.startswith(pfx)]
    records, bad_blobs = build_records(rows, args, extras, parents, warn)

    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(tmp, a.out)

    by_pkg = Counter(top_package(r["module"]) for r in records)
    mods_by_pkg = Counter(top_package(m) for m in modules)
    tauceti = sum(1 for r in records if r["module"] == "TauCeti" or r["module"].startswith("TauCeti."))
    mathlib = sum(1 for r in records if r["is_mathlib"])
    manifest = {}
    for kv in a.meta:
        k, _, v = kv.partition("=")
        manifest[k] = v
    manifest.update({
        "extractor": "doc-gen4 docInfo facet -> api-docs.db -> extract.py",
        "extractor_script": os.path.basename(__file__),
        "extractor_sha256": sha256_file(os.path.abspath(__file__)),
        "built_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "decls_file": os.path.basename(a.out),
        "decls_sha256": sha256_file(a.out),
        "decls_size_bytes": os.path.getsize(a.out),
        "counts": {
            "tauceti": tauceti,
            "mathlib": mathlib,
            "other": len(records) - tauceti - mathlib,
            "total": len(records),
            "by_package": dict(sorted(by_pkg.items())),
            "name_info_rows": len(rows),
            "undecodable_blobs": bad_blobs,
        },
        "modules": {"total": len(modules), "by_package": dict(sorted(mods_by_pkg.items()))},
        "includes": {
            "tauceti": "TauCeti" in by_pkg,
            "mathlib": "Mathlib" in by_pkg,
            "lean_core": all(p in by_pkg for p in ("Init", "Lean", "Std")),
            "lake": "Lake" in by_pkg,
            "batteries": "Batteries" in by_pkg,
        },
        "fields": ["name", "kind", "module", "file", "line", "signature", "docstring", "is_mathlib", "hash"],
        "hash": "sha256(name + '\\n' + signature + '\\n' + docstring), utf-8, hex",
        "kinds": sorted(set(r["kind"] for r in records)),
    })
    mpath = a.manifest or os.path.join(os.path.dirname(os.path.abspath(a.out)), "MANIFEST.json")
    with open(mpath + ".tmp", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(mpath + ".tmp", mpath)
    print(f"wrote {len(records)} records to {a.out} (tauceti={tauceti} mathlib={mathlib} other={len(records) - tauceti - mathlib}); manifest {mpath}")
    if bad_blobs:
        warn(f"{bad_blobs} declarations had undecodable RenderedCode blobs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
