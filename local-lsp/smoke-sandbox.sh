#!/usr/bin/env bash
# Run a command inside a bubblewrap sandbox shaped like TauCetiBench's (sandbox.py `Sandbox.argv`
# plus the mounts and env the runner adds in `_execute`): tmpfs root, ro /usr and /etc, fresh
# /dev /proc /tmp, --clearenv with explicit --setenv, every namespace unshared, no network, no
# user namespaces. Nothing from the host HOME is visible except what is listed below.
#
# Usage:
#   smoke-sandbox.sh [--toolchain rc2|rc3] [--run-dir DIR] [--net] -- CMD [ARGS...]
#
# Binds (the list the bench must reproduce for the LSP arms; everything else is sandbox.py's base):
#   rw  $CHECKOUT                       the TauCeti checkout (repo + its .lake, like the bench's repo and pool .lake)
#   rw  $RUN_DIR                        run dir: HOME, CODEX_HOME, CLAUDE_CONFIG_DIR, logs (like paths.root)
#   rw  $CACHES/lake, $CACHES/mathlib   LAKE_CACHE_DIR / MATHLIB_CACHE_DIR (the bench binds its own rw)
#   ro  $ELAN_HOME                      ~/.elan (toolchains)
#   ro  $TOOLS                          ~/.local/opt/tauceti-tools (lean-beam wrappers + runtime, lean-lsp-mcp venv)
#   ro  $BIN_DIR -> /opt/tauceti-bench/bin   staged bin dir (mcp_probe.py, launch.sh, codex/claude symlinks)
#   ro  ~/.local/share/claude           only when `claude` is the command (the bench does the same)
set -euo pipefail

SMOKE_ROOT="${SMOKE_ROOT:-$HOME/.local/share/tauceti-lsp-smoke}"
TOOLS="${TOOLS:-$HOME/.local/opt/tauceti-tools}"
ELAN_HOME="${ELAN_HOME:-$HOME/.elan}"
SANDBOX_BIN_DIR=/opt/tauceti-bench/bin
TOOLCHAIN=rc3
RUN_DIR=""
NET=0
CODEX_AUTH=0
CLAUDE_AUTH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --toolchain) TOOLCHAIN="$2"; shift 2 ;;
    --run-dir) RUN_DIR="$2"; shift 2 ;;
    --net) NET=1; shift ;;
    --codex-auth) CODEX_AUTH=1; shift ;;     # bind ~/.codex/auth.json rw into the run's CODEX_HOME (bench: _stage_credentials)
    --claude-auth) CLAUDE_AUTH=1; shift ;;   # bind ~/.claude/.credentials.json rw into the run's CLAUDE_CONFIG_DIR
    --) shift; break ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
[ $# -gt 0 ] || { echo "usage: $0 [--toolchain rc2|rc3] [--run-dir DIR] -- CMD..." >&2; exit 2; }
CHECKOUT="$SMOKE_ROOT/$TOOLCHAIN/TauCeti"
[ -d "$CHECKOUT/.lake" ] || { echo "no built checkout at $CHECKOUT" >&2; exit 1; }
CACHES="$SMOKE_ROOT/caches"
mkdir -p "$CACHES/lake" "$CACHES/mathlib" "$CHECKOUT/.lake/tmp"
if [ -z "$RUN_DIR" ]; then
  RUN_DIR="$SMOKE_ROOT/runs/$(date -u +%Y%m%dT%H%M%SZ)-$TOOLCHAIN-$$"
fi
mkdir -p "$RUN_DIR/home" "$RUN_DIR/codex" "$RUN_DIR/claude" "$RUN_DIR/logs"
[ -s "$RUN_DIR/codex/config.toml" ] || : > "$RUN_DIR/codex/config.toml"   # the bench writes an empty config.toml
printf '[user]\n\tname = TauCetiWorker\n\temail = noreply@github.com\n[safe]\n\tdirectory = *\n' > "$RUN_DIR/home/.gitconfig"

# Staged bin dir, like runner._stage_bin: launch.sh (exec "$@"), the probe, and provider symlinks.
BIN_DIR="$RUN_DIR/bin"
mkdir -p "$BIN_DIR"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$HERE/mcp_probe.py" "$BIN_DIR/mcp_probe.py"
rm -rf "$BIN_DIR/cases"; cp -r "$HERE/cases" "$BIN_DIR/cases"   # the case scripts run from the fixed sandbox path
printf '#!/bin/bash\nset -e\nexec "$@"\n' > "$BIN_DIR/launch.sh"
chmod 755 "$BIN_DIR/launch.sh" "$BIN_DIR/mcp_probe.py"
CLAUDE_BIN="$(readlink -f "$(command -v claude 2>/dev/null || true)" 2>/dev/null || true)"
EXTRA_MOUNTS=()
if [ "$CODEX_AUTH" = 1 ]; then
  : > "$RUN_DIR/codex/auth.json"
  printf '# TauCetiBench: no user config reaches a subject; model and sandbox come from argv.\n' > "$RUN_DIR/codex/config.toml"
  EXTRA_MOUNTS+=(--bind "${CODEX_HOME:-$HOME/.codex}/auth.json" "$RUN_DIR/codex/auth.json")
fi
if [ "$CLAUDE_AUTH" = 1 ]; then
  : > "$RUN_DIR/claude/.credentials.json"
  EXTRA_MOUNTS+=(--bind "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.credentials.json" "$RUN_DIR/claude/.credentials.json")
fi
if [ -n "$CLAUDE_BIN" ]; then
  ln -sf "$CLAUDE_BIN" "$BIN_DIR/claude"
  CLAUDE_SHARE="$(dirname "$(dirname "$CLAUDE_BIN")")"   # ~/.local/share/claude
  case "$1" in *claude*) EXTRA_MOUNTS+=(--ro-bind "$CLAUDE_SHARE" "$CLAUDE_SHARE") ;; esac
fi

ENV=(
  HOME="$RUN_DIR/home"
  PATH="$SANDBOX_BIN_DIR:$ELAN_HOME/bin:/usr/local/bin:/usr/bin:/bin"
  ELAN_HOME="$ELAN_HOME"
  LANG=C.UTF-8
  TMPDIR="$CHECKOUT/.lake/tmp"
  MATHLIB_CACHE_DIR="$CACHES/mathlib"
  LAKE_CACHE_DIR="$CACHES/lake"
  LAKE_ARTIFACT_CACHE=true
  LAKE_RESTORE_ARTIFACTS=true
  TAUCETI_BENCH_REPO="$CHECKOUT"
  TAUCETI_BENCH_RUN="$RUN_DIR"
  TAUCETI_PHASE=fix
  TAUCETI_ROUND_ID="$(basename "$RUN_DIR")"
  DISABLE_AUTOUPDATER=1
  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
  CLAUDE_CONFIG_DIR="$RUN_DIR/claude"
  CODEX_HOME="$RUN_DIR/codex"
  SMOKE_CHECKOUT="$CHECKOUT"
  SMOKE_TOOLS="$TOOLS"
  SMOKE_RUN_DIR="$RUN_DIR"
)
SETENV=()
for kv in "${ENV[@]}"; do SETENV+=(--setenv "${kv%%=*}" "${kv#*=}"); done

NETFLAG=(--unshare-net)
RESOLVED=()
if [ "$NET" = 1 ]; then NETFLAG=(); [ -d /run/systemd/resolve ] && RESOLVED=(--ro-bind /run/systemd/resolve /run/systemd/resolve); fi

echo "sandbox: toolchain=$TOOLCHAIN checkout=$CHECKOUT run_dir=$RUN_DIR" >&2
exec /usr/bin/bwrap \
  --tmpfs / \
  --ro-bind /usr /usr \
  --ro-bind /etc /etc \
  --symlink usr/bin /bin \
  --symlink usr/sbin /sbin \
  --symlink usr/lib /lib \
  --symlink usr/lib64 /lib64 \
  --dev /dev \
  --proc /proc \
  --tmpfs /tmp \
  "${RESOLVED[@]}" \
  --bind "$CHECKOUT" "$CHECKOUT" \
  --bind "$CACHES/lake" "$CACHES/lake" \
  --bind "$CACHES/mathlib" "$CACHES/mathlib" \
  --bind "$RUN_DIR" "$RUN_DIR" \
  --ro-bind "$BIN_DIR" "$SANDBOX_BIN_DIR" \
  --ro-bind "$ELAN_HOME" "$ELAN_HOME" \
  --ro-bind "$TOOLS" "$TOOLS" \
  "${EXTRA_MOUNTS[@]}" \
  --remount-ro / \
  --clearenv \
  "${SETENV[@]}" \
  --chdir "$CHECKOUT" \
  --unshare-user --unshare-ipc --unshare-pid --unshare-uts --unshare-cgroup \
  "${NETFLAG[@]}" \
  --disable-userns --die-with-parent --new-session \
  -- "$SANDBOX_BIN_DIR/launch.sh" "$@"
