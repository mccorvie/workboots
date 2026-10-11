#!/usr/bin/env bash
# Smoke-test the explore service over its unix socket (or TAUCETI_EXPLORE_URL).
#   client-smoke.sh ['query' ...]
set -euo pipefail
sock_dir="${TAUCETI_EXPLORE_SOCKET_DIR:-${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/tauceti-explore}"
pin="${TAUCETI_EXPLORE_PIN:-}"; sock="$sock_dir${pin:+/$pin}/explore.sock"
url="${TAUCETI_EXPLORE_URL:-http://127.0.0.1:8089}"
k="${TAUCETI_SEARCH_K:-10}"
curl_args=(--fail --silent --show-error --connect-timeout 2 --max-time 30 --get)
if [[ -S "$sock" ]]; then curl_args+=(--unix-socket "$sock"); url="http://localhost"; echo "using socket $sock" >&2; else echo "using $url" >&2; fi
echo "== /health"
curl "${curl_args[@]}" "$url/health" | jq -c '{ok, tool, default_pin: .manifest.default_pin, pins: (.manifest.pins | keys), embedding: .manifest.models.embedding.revision[0:12], reranker: .manifest.models.reranker.revision[0:12]}'
queries=("$@"); (( ${#queries[@]} )) || queries=("continuous image of a compact set is compact" "List.map")
for q in "${queries[@]}"; do
  echo "== /search q=$q k=$k"
  curl "${curl_args[@]}" --data-urlencode "q=$q" --data-urlencode "k=$k" "$url/search" | jq -c '{pin, ms, hits: [.hits[] | {name, kind, score}]}'
done
