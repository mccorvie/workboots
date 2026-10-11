#!/usr/bin/env bash
# Write (but do not enable or start) the systemd --user unit for the explore service.
#   install-service.sh [--pin <pin>]... [--port 8089]
# Afterwards, by hand:  systemctl --user daemon-reload && systemctl --user enable --now tauceti-explore.service
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pins=(); port=8089
while (( $# )); do
  case "$1" in
    --pin) pins+=("$2"); shift 2;;
    --port) port="$2"; shift 2;;
    *) echo "unknown arg $1" >&2; exit 64;;
  esac
done
pin_args=""
for p in "${pins[@]:-}"; do [[ -n "$p" ]] && pin_args+=" --pin $p"; done
unit_dir="$HOME/.config/systemd/user"; mkdir -p "$unit_dir"
sed -e "s|__PINS__|${pin_args}|" -e "s|__PORT__|${port}|" "$here/systemd/tauceti-explore.service" > "$unit_dir/tauceti-explore.service"
[[ -x "$here/.venv/bin/python" ]] || echo "warning: $here/.venv missing; run 'uv sync' in $here first" >&2
echo "wrote $unit_dir/tauceti-explore.service (pins:${pin_args:- all under ~/.local/share/tauceti-explore}; port $port)"
echo "not enabled. To start:  systemctl --user daemon-reload && systemctl --user enable --now tauceti-explore.service"
echo "socket will be: ${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/tauceti-explore/<pin>/explore.sock"
