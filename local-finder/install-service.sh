#!/usr/bin/env bash
# Install (but do not enable/start) the tauceti-finder systemd --user unit.
#
#   ./install-service.sh                 # write unit + env file, daemon-reload
#   ./install-service.sh --enable        # additionally: systemctl --user enable --now
#
# The env file ~/.config/tauceti-finder/finder.env holds the pin list and device; it is
# created once from the indexes found under ~/.local/share/tauceti-finder/ and never overwritten.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
unit_src="$here/systemd/tauceti-finder.service"
unit_dir="$HOME/.config/systemd/user"
env_dir="$HOME/.config/tauceti-finder"
env_file="$env_dir/finder.env"
data_root="${TAUCETI_FINDER_DATA:-$HOME/.local/share/tauceti-finder}"
enable=0
for a in "$@"; do
  case "$a" in
    --enable) enable=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

[ -x "$here/.venv/bin/python" ] || { echo "no venv at $here/.venv; run: uv sync" >&2; exit 1; }

mkdir -p "$unit_dir" "$env_dir"
install -m 0644 "$unit_src" "$unit_dir/tauceti-finder.service"
echo "wrote $unit_dir/tauceti-finder.service"

if [ ! -f "$env_file" ]; then
  pins=""
  for m in "$data_root"/*/MANIFEST.json; do
    [ -f "$m" ] || continue
    pins="$pins $(basename "$(dirname "$m")")"
  done
  pins="${pins# }"
  cat > "$env_file" <<ENV
# tauceti-finder service configuration (read by systemd via EnvironmentFile).
# Space-separated pin directory names under $data_root; the first is the default.
TAUCETI_FINDER_PINS=${pins}
# cuda:0 for the production build; cpu only for validation (fp32, ~33 GB RAM, slow queries).
TAUCETI_FINDER_DEVICE=cuda:0
# Optional loopback TCP fallback for host runs (TAUCETI_FINDER_URL in the worker); unset = socket only.
#TAUCETI_FINDER_PORT=8090
ENV
  echo "wrote $env_file (pins: '${pins:-<none found>}')"
else
  echo "kept existing $env_file"
fi

systemctl --user daemon-reload
echo "daemon-reload done. Unit is installed but NOT enabled."
if [ "$enable" = 1 ]; then
  systemctl --user enable --now tauceti-finder.service
  systemctl --user --no-pager status tauceti-finder.service || true
else
  cat <<MSG
Next steps (when the GPU driver is back and the pins are built):
  systemctl --user enable --now tauceti-finder.service
  journalctl --user -u tauceti-finder.service -f
  $here/client-smoke.sh
MSG
fi
