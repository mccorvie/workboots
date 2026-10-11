#!/usr/bin/env bash
# Smoke-test the finder service through its unix socket (or a TCP URL).
#   ./client-smoke.sh                       # $XDG_RUNTIME_DIR/tauceti-finder/finder.sock
#   ./client-smoke.sh --socket /path/to.sock
#   ./client-smoke.sh --url http://127.0.0.1:8090
#   ./client-smoke.sh ... --pin b3-11bc13335fb9 --q "continuous image of a compact set is compact" --k 3
set -euo pipefail
sock="${TAUCETI_FINDER_SOCKET_DIR:-${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/tauceti-finder}/finder.sock"
url=""; pin=""; q="continuous image of a compact set is compact"; k=3
while [ $# -gt 0 ]; do
  case "$1" in
    --socket) sock="$2"; shift 2 ;;
    --url) url="$2"; shift 2 ;;
    --pin) pin="$2"; shift 2 ;;
    --q) q="$2"; shift 2 ;;
    --k) k="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [ -n "$url" ]; then
  curl_base=(curl --fail --silent --show-error --max-time 30)
  base="$url"
else
  [ -S "$sock" ] || { echo "no socket at $sock (service down?)" >&2; exit 1; }
  curl_base=(curl --fail --silent --show-error --max-time 30 --unix-socket "$sock")
  base="http://finder"
fi
pp() { if command -v jq >/dev/null 2>&1; then jq .; else cat; echo; fi; }
echo "== GET /health"
"${curl_base[@]}" --get ${pin:+--data-urlencode "pin=$pin"} "$base/health" | pp | head -40
echo "== GET /search  q=$q k=$k ${pin:+pin=$pin}"
"${curl_base[@]}" --get --data-urlencode "q=$q" --data-urlencode "k=$k" ${pin:+--data-urlencode "pin=$pin"} \
  -H "X-TauCeti-Worker: smoke" -H "X-TauCeti-Phase: fix" "$base/search" | pp
