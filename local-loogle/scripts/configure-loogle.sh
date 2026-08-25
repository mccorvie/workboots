#!/usr/bin/env bash

# Check out the relevant projects

export TAUCETI_LOOGLE_ROOT="$HOME/.local/share/tauceti-loogle"
export TAUCETI_LOOGLE_STATE="$HOME/.local/state/tauceti-loogle"

export TAUCETI_REPO="$TAUCETI_LOOGLE_ROOT/TauCeti"
export LOOGLE_REPO="$TAUCETI_LOOGLE_ROOT/loogle"
export LOOGLE_INDEX="$TAUCETI_LOOGLE_STATE/TauCeti.LoogleAll.loogle-index"

mkdir -p "$TAUCETI_LOOGLE_ROOT" "$TAUCETI_LOOGLE_STATE"

git clone https://github.com/TauCetiProject/TauCeti.git "$TAUCETI_REPO"
git clone https://github.com/nomeata/loogle.git "$LOOGLE_REPO"


# build loogle with the appropriate toolchain

cp "$TAUCETI_REPO/lean-toolchain" "$LOOGLE_REPO/lean-toolchain"

(
  cd "$LOOGLE_REPO"
  lake build
)

# Ensure all of Tau Ceti is in scope for loogle

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

# Import the Mathlib oleans and build Tau Ceti

(
  cd "$TAUCETI_REPO"
  lake exe cache get
  lake build TauCeti.LoogleAll
)

# Build the local index

(
  cd "$TAUCETI_REPO"

  lake env "$LOOGLE_REPO/.lake/build/bin/loogle" \
    --module TauCeti.LoogleAll \
    --index-file "$LOOGLE_INDEX" \
    --index-mode write \
    --max-results 1 \
    'Nat.succ' >/dev/null
)


# Test the local index
(
  cd "$TAUCETI_REPO"

  lake env "$LOOGLE_REPO/.lake/build/bin/loogle" \
    --module TauCeti.LoogleAll \
    --index-file "$LOOGLE_INDEX" \
    --index-mode read \
    --json \
    'List.map' |
    jq .
)