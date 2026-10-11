#!/usr/bin/env bash
# Install Lean Beam from source at a pinned commit under ~/.local/opt/tauceti-tools/beam (no sudo,
# nothing under /opt), prebuilding its Lean plugin bundle for the TauCeti corpus toolchains.
#
#   bin/     lean-beam, lean-beam-search, lean-beam-mcp wrappers  (BEAM_BIN_HOME)
#   share/   versions/<payload>/..., current -> link, bundles cache  (BEAM_INSTALL_ROOT)
#   src/     the pinned checkout (the installer builds beam-cli etc. in src/.lake with Beam's own
#            lean-toolchain, v4.35.0-rc4 at this pin; elan fetches it on demand, so this step needs
#            network once)
#
# Re-running is idempotent for the same pin. Takes about 10-15 minutes on this host.
set -euo pipefail
BEAM_PIN="${BEAM_PIN:-4b045750bd7ba359abc881145fcfa9ca9323f79c}"   # leanprover/lean-beam main, 2026-10-08
TOOLS="${TOOLS:-$HOME/.local/opt/tauceti-tools}"
TOOLCHAINS=("${@:-leanprover/lean4:v4.34.0-rc2 leanprover/lean4:v4.35.0-rc3}")
[ $# -gt 0 ] || TOOLCHAINS=(leanprover/lean4:v4.34.0-rc2 leanprover/lean4:v4.35.0-rc3)
export PATH="$HOME/.elan/bin:$PATH"
command -v elan >/dev/null || { echo "elan is required" >&2; exit 1; }

mkdir -p "$TOOLS/beam"
if [ ! -d "$TOOLS/beam/src/.git" ]; then
  git clone --quiet https://github.com/leanprover/lean-beam "$TOOLS/beam/src"
fi
git -C "$TOOLS/beam/src" fetch --quiet origin
git -C "$TOOLS/beam/src" checkout --quiet "$BEAM_PIN"
echo "$BEAM_PIN" > "$TOOLS/beam/PINNED_COMMIT"

args=(--dont-ask)   # no agent skills, no MCP client registration: the bench injects the server per run
for tc in "${TOOLCHAINS[@]}"; do args+=(--toolchain "$tc"); done
cd "$TOOLS/beam/src"
time BEAM_BIN_HOME="$TOOLS/beam/bin" BEAM_INSTALL_ROOT="$TOOLS/beam/share" \
  ./scripts/install-beam.sh "${args[@]}" </dev/null

"$TOOLS/beam/bin/lean-beam" --version
"$TOOLS/beam/bin/lean-beam-mcp" --version
