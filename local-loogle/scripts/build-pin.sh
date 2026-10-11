#!/usr/bin/env bash
# Build one frozen Loogle index for a corpus pin. Idempotent: finished stages are skipped.
# usage: build-pin.sh <pin>   e.g. build-pin.sh b3-11bc13335fb9
# Needs ~/.local/share/tauceti-index/<pin>/MANIFEST.json (identity). Optional env:
#   SEED_CHECKOUT  built TauCeti checkout at the pin to copy instead of cloning (b3 uses the lsp-smoke one)
#   LOOGLE_COMMIT  nomeata/loogle commit (default 9f11169a...)
set -euo pipefail
pin=${1:?usage: build-pin.sh <pin>}
export PATH="$HOME/.elan/bin:$PATH"
here=$(cd "$(dirname "$0")/.." && pwd)
ident="$HOME/.local/share/tauceti-index/$pin/MANIFEST.json"
root="$HOME/.local/share/tauceti-loogle/$pin"
state="$HOME/.local/state/tauceti-loogle/$pin"
idx="$state/TauCeti.LoogleAll.loogle-index"
LOOGLE_COMMIT=${LOOGLE_COMMIT:-9f11169aaebf1ed1e7dcc4077f2aafe0fcf66fd0}
sha=$(jq -r .tauceti_sha "$ident")
mkdir -p "$root" "$state" "$HOME/.local/share/tauceti-loogle/logs"
tj="$root/timings.json"; [[ -f $tj ]] || echo '{}' > "$tj"
stamp() { jq --arg k "$1" --argjson v "$2" '.[$k]=$v' "$tj" > "$tj.tmp" && mv "$tj.tmp" "$tj"; }
have() { jq -e --arg k "$1" 'has($k)' "$tj" >/dev/null; }
now() { date +%s.%N; }
lap() { python3 -c "print(round($(now)-$1,1))"; }

# 1. checkout / copy
if ! have checkout_copy_s; then
  t=$(now)
  rm -rf "$root/TauCeti" "$root/loogle"
  if [[ -n ${SEED_CHECKOUT:-} ]]; then
    [[ $(git -C "$SEED_CHECKOUT" rev-parse HEAD) == "$sha" ]] || { echo "seed is not at $sha" >&2; exit 1; }
    cp -a --reflink=auto "$SEED_CHECKOUT" "$root/TauCeti"
  else
    git clone --reference /srv/tauceti/records/git/TauCeti.git https://github.com/TauCetiProject/TauCeti.git "$root/TauCeti"
    git -C "$root/TauCeti" checkout --detach "$sha"
  fi
  git -C "$root/TauCeti" reset --hard -q "$sha"   # drops only tracked edits; build outputs are untracked/ignored
  git clone --reference ~/.local/share/tauceti-loogle/loogle https://github.com/nomeata/loogle.git "$root/loogle"
  git -C "$root/loogle" checkout --detach "$LOOGLE_COMMIT"
  cp "$root/TauCeti/lean-toolchain" "$root/loogle/lean-toolchain"
  stamp checkout_copy_s "$(lap "$t")"
fi

# 2. loogle build with the pin's toolchain
if ! have loogle_build_s; then
  t=$(now)
  (cd "$root/loogle" && lake build)
  stamp loogle_build_s "$(lap "$t")"
fi

# 3. TauCeti build: aggregate module, Mathlib cache, build
agg="$root/TauCeti/TauCeti/LoogleAll.lean"
if ! have tauceti_build_s; then
  t=$(now)
  {
    printf 'module\n\npublic import Mathlib\n'
    find "$root/TauCeti/TauCeti" -type f -name '*.lean' ! -name LoogleAll.lean -print0 | sort -z |
      while IFS= read -r -d '' f; do m=${f#"$root/TauCeti/"}; m=${m%.lean}; printf 'public import %s\n' "${m//\//.}"; done
    printf '\n/-! Aggregate module so Loogle indexes all of Tau Ceti. -/\n\nset_option linter.style.longFile 0\n'
  } > "$agg.tmp"
  mv "$agg.tmp" "$agg"
  (cd "$root/TauCeti" && lake exe cache get && lake build TauCeti.LoogleAll)
  stamp tauceti_build_s "$(python3 -c "print(round($(lap "$t")+${EXTRA_TAUCETI_S:-0},1))")"  # EXTRA_TAUCETI_S: time of an earlier failed attempt
fi
modules=$(grep -c '^public import TauCeti' "$agg")

# 4. index write
if ! have index_write_s || [[ ! -s $idx ]]; then
  t=$(now)
  rm -f "$idx"
  (cd "$root/TauCeti" && lake env "$root/loogle/.lake/build/bin/loogle" --module TauCeti.LoogleAll \
     --index-file "$idx" --index-mode write --max-results 1 'Nat.succ' >/dev/null)
  stamp index_write_s "$(lap "$t")"
fi
(cd "$root/TauCeti" && lake env "$root/loogle/.lake/build/bin/loogle" --module TauCeti.LoogleAll \
   --index-file "$idx" --index-mode read --json 'List.map' | jq -e '.hits|length>0' >/dev/null)

# 5. manifest
jq -n --slurpfile id "$ident" --slurpfile tm "$tj" \
  --arg lc "$(git -C "$root/loogle" rev-parse HEAD)" --arg idx "$idx" \
  --arg sum "$(sha256sum "$idx" | cut -d' ' -f1)" --argjson bytes "$(stat -c %s "$idx")" \
  --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --argjson mods "$modules" '
  {tool:"loogle", pin:$id[0].pin, tauceti_sha:$id[0].tauceti_sha, mathlib_rev:$id[0].mathlib_rev,
   lean_toolchain:$id[0].lean_toolchain, loogle_commit:$lc,
   index_file:{path:$idx, sha256:$sum, bytes:$bytes}, built_at:$at,
   timings:{checkout_copy_s:$tm[0].checkout_copy_s, loogle_build_s:$tm[0].loogle_build_s,
            tauceti_build_s:$tm[0].tauceti_build_s, index_write_s:$tm[0].index_write_s},
   modules_imported:$mods}' > "$root/MANIFEST.json"
mkdir -p "$here/manifests"; cp "$root/MANIFEST.json" "$here/manifests/$pin.json"
echo "done: $root/MANIFEST.json"
