#!/usr/bin/env python3
"""Add Lean Finder's exact `typeFull` (from dump_typefull.lean) to a decls.jsonl.

    scripts/merge_typefull.py decls.jsonl typefull.jsonl > decls.typefull.jsonl

Rows are matched by `name`; unmatched rows keep no `typeFull` (build_finder then falls back to
rebuilding it from the doc-gen4 signature, see finder_common.signature_to_type_full) and are counted
on stderr.  Point build_finder.py at the result with --decls.
"""
import json
import sys

def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    tf = {}
    with open(sys.argv[2], encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            tf[r["name"]] = r["typeFull"]
    hit = miss = 0
    for line in open(sys.argv[1], encoding="utf-8"):
        d = json.loads(line)
        t = tf.get(d["name"])
        if t is not None:
            d["typeFull"] = t
            hit += 1
        else:
            miss += 1
        sys.stdout.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"typeFull merged for {hit} rows; {miss} rows without (signature fallback)", file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
