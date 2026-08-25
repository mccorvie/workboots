#!/usr/bin/env bash
set -euo pipefail

if (( $# == 0 )); then
  echo "usage: tauceti-loogle '<loogle query>'" >&2
  exit 64
fi

query="$*"
socket="${TAUCETI_LOOGLE_SOCKET:-/run/tauceti-loogle/loogle.sock}"
url="${TAUCETI_LOOGLE_URL:-http://127.0.0.1:8088/json}"
worker="${TAUCETI_WORKER_ID:-unknown}"


curl_args=(
  --fail
  --silent
  --show-error
  --connect-timeout 2
  --max-time 20
  --retry 2
  --retry-max-time 12
  --get
  --data-urlencode "q=$query"
  --header "User-Agent: taucetiworker-loogle/1"  
  --header "X-Loogle-Client: taucetiworker/1"
  --header "X-TauCeti-Worker: $worker"
)

if [[ -S "$socket" ]]; then
  curl_args+=(--unix-socket "$socket")
  url="http://localhost/json"
fi

curl "${curl_args[@]}" "$url" | jq -c .