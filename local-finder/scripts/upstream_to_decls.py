#!/usr/bin/env python3
"""Map rows of Lean Finder's public Mathlib corpus into our decls.jsonl schema.

Used for CPU validation while the shared extraction is still running, and to
prove the adapter is lossless: for every row we assert that
format_entry(adapt_decl(decl_row)) == format_entry(upstream_row) byte for byte.

    scripts/upstream_to_decls.py --corpus ~/.local/share/tauceti-finder/upstream-corpus/mathlib4_28_0.jsonl \
        --out /path/decls.jsonl --stride 1123 --limit 200
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from finder_common import adapt_decl, format_entry, read_jsonl  # noqa: E402


def to_decl(u: dict) -> dict:
    module = u["path"].replace("/", ".")
    doc = u.get("docString")
    return {
        "name": u["name"],
        "kind": u["kind"],
        "module": module,
        "file": u["path"] + ".lean",
        "line": None,
        "signature": u["typeFull"],
        "docstring": doc if doc is not None else "",
        "is_mathlib": True,
        "hash": hashlib.sha256((u["name"] + "\n" + u["typeFull"]).encode()).hexdigest()[:16],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--row-ids-out", type=Path, default=None, help="write the upstream row indices used (one per line)")
    args = ap.parse_args()

    rows = read_jsonl(args.corpus)
    picked = [(i, rows[i]) for i in range(0, len(rows), args.stride)]
    if args.limit:
        picked = picked[: args.limit]
    mismatches = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for _, u in picked:
            d = to_decl(u)
            if format_entry(adapt_decl(d)) != format_entry(u):
                mismatches += 1
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    if args.row_ids_out:
        args.row_ids_out.write_text("".join(f"{i}\n" for i, _ in picked))
    print(json.dumps({"rows": len(picked), "format_roundtrip_mismatches": mismatches, "out": str(args.out)}))
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
