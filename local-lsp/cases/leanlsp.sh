#!/usr/bin/env bash
# lean-lsp-mcp smoke cases. Runs INSIDE the sandbox (smoke-sandbox.sh exports SMOKE_CHECKOUT,
# SMOKE_TOOLS, SMOKE_RUN_DIR). Positions are 1-based for this server.
set -euo pipefail
CK="$SMOKE_CHECKOUT"; TOOLS="$SMOKE_TOOLS"; OUT="$SMOKE_RUN_DIR/logs"
PROBE=/opt/tauceti-bench/bin/mcp_probe.py
SERVER="$TOOLS/bin/lean-lsp-mcp"
F=TauCeti/Analysis/PSeries.lean
DISABLED=lean_leansearch,lean_loogle,lean_leanfinder,lean_hammer_premise
cd "$CK"
git checkout -q -- "$F" TauCeti/MeasureTheory/Measure/Measurability.lean
echo "== env seen by the server (sanity)"; env | sort | grep -E '^(HOME|PATH|ELAN_HOME|LANG|TMPDIR|LAKE_|MATHLIB_|CODEX_HOME)' || true
echo "== 1. tools/list, default and with remote search tools disabled"
printf '%s\n' '{"op":"list","id":"list-default"}' | "$PROBE" --timeout 120 --log "$OUT/leanlsp-list.stderr" --env LEAN_PROJECT_PATH="$CK" -- "$SERVER" | tee "$OUT/leanlsp-list-default.jsonl"
printf '%s\n' '{"op":"list","id":"list-disabled"}' | "$PROBE" --timeout 120 --log "$OUT/leanlsp-list.stderr" --env LEAN_PROJECT_PATH="$CK" --env LEAN_MCP_DISABLED_TOOLS="$DISABLED" -- "$SERVER" | tee "$OUT/leanlsp-list-disabled.jsonl"

echo "== 2. goal (cold), diagnostics, edit + diagnostics, multi_attempt, parallel multi_attempt"
cat > "$OUT/leanlsp-cases.jsonl" <<EOF
{"op":"note","text":"cold: lean_goal at PSeries.lean:53 (1-based)"}
{"op":"call","id":"goal-cold","name":"lean_goal","args":{"file_path":"$F","line":53}}
{"op":"call","id":"goal-warm","name":"lean_goal","args":{"file_path":"$F","line":50,"column":5}}
{"op":"call","id":"diag-clean","name":"lean_diagnostic_messages","args":{"file_path":"$F"}}
{"op":"shell","cmd":"printf '\\\\ntheorem smoke_bad : (1 : Nat) = 2 := by rfl\\\\n' >> $F"}
{"op":"call","id":"diag-after-edit","name":"lean_diagnostic_messages","args":{"file_path":"$F"}}
{"op":"call","id":"diag-after-edit-errors","name":"lean_diagnostic_messages","args":{"file_path":"$F","severity":"error"}}
{"op":"shell","cmd":"git checkout -q -- $F"}
{"op":"call","id":"diag-restored","name":"lean_diagnostic_messages","args":{"file_path":"$F"}}
{"op":"call","id":"multi3","name":"lean_multi_attempt","args":{"file_path":"$F","line":54,"snippets":["nlinarith [Real.pi_lt_d2, Real.pi_pos]","norm_num","linarith"]}}
{"op":"par","calls":[{"id":"par-a","name":"lean_multi_attempt","args":{"file_path":"$F","line":54,"snippets":["nlinarith [Real.pi_lt_d2, Real.pi_pos]","simp"]}},{"id":"par-b","name":"lean_multi_attempt","args":{"file_path":"$F","line":54,"snippets":["positivity","linarith"]}}]}
{"op":"call","id":"hover","name":"lean_hover_info","args":{"file_path":"$F","line":47,"column":11}}
{"op":"call","id":"build","name":"lean_build","args":{}}
EOF
/usr/bin/time -v -o "$OUT/leanlsp-cases.time" "$PROBE" --timeout 900 --log "$OUT/leanlsp-cases.stderr" --rss "$OUT/leanlsp-cases.rss" \
  --env LEAN_PROJECT_PATH="$CK" --env LEAN_MCP_DISABLED_TOOLS="$DISABLED" --env LEAN_LOG_LEVEL=INFO \
  -- "$SERVER" < "$OUT/leanlsp-cases.jsonl" | tee "$OUT/leanlsp-cases.out.jsonl"
git checkout -q -- "$F"
echo "== done; peak rss kB: $(cat "$OUT/leanlsp-cases.rss")"
