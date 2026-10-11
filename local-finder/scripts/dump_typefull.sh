#!/usr/bin/env bash
# Produce typefull.jsonl (exact `ppExpr` types, width 120) for a pin and merge it into a new decls file.
#
#   scripts/dump_typefull.sh b3-11bc13335fb9
#
# Reads    ~/.local/share/tauceti-index/<pin>/decls.jsonl and the pin's scratch checkout (TauCeti/, built by
#          ~/lean/workboots/index/build_extraction.sh: Mathlib and TauCeti oleans must be present)
# Writes   ~/.local/share/tauceti-index/<pin>/typefull.jsonl and decls.typefull.jsonl
# Then:    build_finder.py <pin> --decls ~/.local/share/tauceti-index/<pin>/decls.typefull.jsonl
# Single-threaded; ~4 min per 144k constants (about 10 min for everything). Imports every module named in decls.jsonl.
set -euo pipefail
pin="${1:?usage: dump_typefull.sh <pin>}"
root="${TAUCETI_INDEX_DATA:-$HOME/.local/share/tauceti-index}/$pin"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$root/TauCeti"
[ -f "$root/decls.jsonl" ] || { echo "no $root/decls.jsonl" >&2; exit 1; }
[ -d "$src/.lake/packages/mathlib/.lake/build/lib/lean" ] || { echo "no built Mathlib under $src" >&2; exit 1; }
tc="$(cat "$src/lean-toolchain")"; tc="${tc#leanprover/lean4:}"
lean_home="$HOME/.elan/toolchains/leanprover--lean4---$tc"
[ -x "$lean_home/bin/lean" ] || { echo "toolchain $tc missing under ~/.elan" >&2; exit 1; }
lp=""
for d in "$src"/.lake/packages/*/.lake/build/lib/lean; do lp="$lp$d:"; done
export LEAN_PATH="$lp$src/.lake/build/lib/lean:$lean_home/lib/lean"
python3 - "$root/decls.jsonl" > "$root/typefull.modules" <<'PY'
import json, sys
mods = sorted({json.loads(l)["module"] for l in open(sys.argv[1], encoding="utf-8") if l.strip()})
print("\n".join(m for m in mods if m))
PY
echo "importing $(wc -l < "$root/typefull.modules") modules with Lean $tc" >&2
(cd /tmp && "$lean_home/bin/lean" --run "$here/dump_typefull.lean" "@$root/typefull.modules" > "$root/typefull.jsonl.tmp")
mv "$root/typefull.jsonl.tmp" "$root/typefull.jsonl"
"$here/merge_typefull.py" "$root/decls.jsonl" "$root/typefull.jsonl" > "$root/decls.typefull.jsonl"
