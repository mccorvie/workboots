#!/usr/bin/env bash

git -C "$TAUCETI_REPO" fetch --prune origin main
git -C "$TAUCETI_REPO" reset --hard origin/main

# construct master import file

aggregate="$TAUCETI_REPO/TauCeti/LoogleAll.lean"
tmp="${aggregate}.tmp"

{
  printf 'module\n\n'
  printf 'public import Mathlib\n'

  find "$TAUCETI_REPO/TauCeti" \
    -type f \
    -name '*.lean' \
    ! -name 'LoogleAll.lean' \
    -print0 |
    sort -z |
    while IFS= read -r -d '' file; do
      module=${file#"$TAUCETI_REPO/"}
      module=${module%.lean}
      module=${module//\//.}
      printf 'public import %s\n' "$module"
    done
    printf 'set_option linter.style.longFile 0\n'
} > "$tmp"

mv "$tmp" "$aggregate"


# Regenerate TauCeti/LoogleAll.lean as above.

cp "$TAUCETI_REPO/lean-toolchain" "$LOOGLE_REPO/lean-toolchain"

(
  cd "$LOOGLE_REPO"
  lake build
)

(
  cd "$TAUCETI_REPO"
  lake exe cache get
  lake build TauCeti.LoogleAll

  lake env "$LOOGLE_REPO/.lake/build/bin/loogle" \
    --module TauCeti.LoogleAll \
    --index-file "$LOOGLE_INDEX" \
    --index-mode write \
    --max-results 1 \
    'Nat.succ' >/dev/null
)

systemctl --user restart tauceti-loogle.service
