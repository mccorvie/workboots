#!/usr/bin/env bash
# Lean Beam smoke cases. Runs INSIDE the sandbox (smoke-sandbox.sh exports SMOKE_CHECKOUT,
# SMOKE_TOOLS, SMOKE_RUN_DIR). Beam positions are 0-based LSP (line, UTF-16 character).
set -euo pipefail
CK="$SMOKE_CHECKOUT"; TOOLS="$SMOKE_TOOLS"; OUT="$SMOKE_RUN_DIR/logs"
PROBE=/opt/tauceti-bench/bin/mcp_probe.py
SERVER="$TOOLS/beam/bin/lean-beam-mcp"
F=TauCeti/Analysis/PSeries.lean
DEP=TauCeti/MeasureTheory/Measure/Measurability.lean          # importee
IMP=TauCeti/Probability/Distributions/Poisson/Measurability.lean  # imports DEP
WS="{\"root\":\"$CK\"}"
cd "$CK"
git checkout -q -- "$F" "$DEP" "$IMP"
rm -rf .beam
echo "== env seen by the server (sanity)"; env | sort | grep -E '^(HOME|PATH|ELAN_HOME|LANG|TMPDIR|LAKE_|MATHLIB_|CODEX_HOME)' || true
echo "== 0. host-style checks inside the sandbox: doctor, --version"
"$TOOLS/beam/bin/lean-beam" doctor 2>&1 | tee "$OUT/beam-doctor.txt" | grep -E 'admission|accepted|bundle ready|bundle source:|daemon status|plugin' || true
"$TOOLS/beam/bin/lean-beam-mcp" --version 2>&1 | head -3

echo "== 1. tools/list"
printf '%s\n' '{"op":"list","id":"list"}' | "$PROBE" --timeout 120 --log "$OUT/beam-list.stderr" -- "$SERVER" | tee "$OUT/beam-list.jsonl"

echo "== 2. sync (cold), 4 parallel run_at, stale version, goals, save + dependent"
# "@V:<path>" placeholders are replaced by the probe with the latest version seen for that path.
cat > "$OUT/beam-cases.jsonl" <<EOF
{"op":"call","id":"version","name":"beam_version","args":{}}
{"op":"note","text":"cold: lean_sync PSeries.lean"}
{"op":"call","id":"sync-cold","name":"lean_sync","args":{"workspace":$WS,"path":"$F","diagnostics_in_result":true}}
{"op":"call","id":"sync-warm","name":"lean_sync","args":{"workspace":$WS,"path":"$F"}}
{"op":"call","id":"goals-before-l53","name":"lean_goals","args":{"workspace":$WS,"path":"$F","version":"@V:$F","line":53,"character":4,"mode":"before"}}
{"op":"call","id":"goals-after-l52","name":"lean_goals","args":{"workspace":$WS,"path":"$F","version":"@V:$F","line":52,"character":4,"mode":"after"}}
{"op":"par","calls":[{"id":"run-nlinarith","name":"lean_run_at","args":{"workspace":$WS,"path":"$F","version":"@V:$F","line":53,"character":4,"text":"nlinarith [Real.pi_lt_d2, Real.pi_pos]"}},{"id":"run-linarith","name":"lean_run_at","args":{"workspace":$WS,"path":"$F","version":"@V:$F","line":53,"character":4,"text":"linarith"}},{"id":"run-norm_num","name":"lean_run_at","args":{"workspace":$WS,"path":"$F","version":"@V:$F","line":53,"character":4,"text":"norm_num"}},{"id":"run-positivity","name":"lean_run_at","args":{"workspace":$WS,"path":"$F","version":"@V:$F","line":53,"character":4,"text":"positivity"}}]}
{"op":"call","id":"run-stale-version","name":"lean_run_at","args":{"workspace":$WS,"path":"$F","version":"@V+100:$F","line":53,"character":4,"text":"linarith"}}
{"op":"call","id":"run-version-0","name":"lean_run_at","args":{"workspace":$WS,"path":"$F","version":0,"line":53,"character":4,"text":"linarith"}}
{"op":"call","id":"hover-l46","name":"lean_hover","args":{"workspace":$WS,"path":"$F","version":"@V:$F","line":46,"character":10}}
{"op":"call","id":"todo","name":"lean_todo","args":{"workspace":$WS,"path":"$F","version":"@V:$F","start_line":0,"start_character":0,"end_line":56,"end_character":0}}
{"op":"note","text":"dependency: importer sees importee's new lemma only after lean_save"}
{"op":"call","id":"imp-sync-0","name":"lean_sync","args":{"workspace":$WS,"path":"$IMP"}}
{"op":"call","id":"imp-check-before","name":"lean_run_at","args":{"workspace":$WS,"path":"$IMP","version":"@V:$IMP","line":28,"character":0,"text":"#check TauCeti.MeasureTheory.smoke_dep_lemma"}}
{"op":"shell","cmd":"sed -i '73i theorem smoke_dep_lemma : True := trivial\\\\n' $DEP && sed -n 70,76p $DEP"}
{"op":"call","id":"dep-sync-edited","name":"lean_sync","args":{"workspace":$WS,"path":"$DEP","diagnostics_in_result":true}}
{"op":"call","id":"dep-save","name":"lean_save","args":{"workspace":$WS,"path":"$DEP"}}
{"op":"call","id":"imp-sync-1","name":"lean_sync","args":{"workspace":$WS,"path":"$IMP","diagnostics_in_result":true}}
{"op":"call","id":"imp-check-after-sync","name":"lean_run_at","args":{"workspace":$WS,"path":"$IMP","version":"@V:$IMP","line":28,"character":0,"text":"#check TauCeti.MeasureTheory.smoke_dep_lemma"}}
{"op":"call","id":"imp-refresh","name":"lean_refresh","args":{"workspace":$WS,"path":"$IMP","diagnostics_in_result":true}}
{"op":"call","id":"imp-check-after-refresh","name":"lean_run_at","args":{"workspace":$WS,"path":"$IMP","version":"@V:$IMP","line":28,"character":0,"text":"#check TauCeti.MeasureTheory.smoke_dep_lemma"}}
{"op":"note","text":"stale after edit: edit PSeries then probe with the old version"}
{"op":"shell","cmd":"printf '\\\\n-- smoke edit\\\\n' >> $F"}
{"op":"call","id":"run-after-disk-edit-old-version","name":"lean_run_at","args":{"workspace":$WS,"path":"$F","version":"@V:$F","line":53,"character":4,"text":"linarith"}}
{"op":"call","id":"update-after-edit","name":"lean_update","args":{"workspace":$WS,"path":"$F"}}
{"op":"call","id":"sync-after-update","name":"lean_sync","args":{"workspace":$WS,"path":"$F"}}
{"op":"call","id":"run-after-update","name":"lean_run_at","args":{"workspace":$WS,"path":"$F","version":"@V:$F","line":53,"character":4,"text":"linarith"}}
{"op":"call","id":"stats","name":"beam_stats","args":{}}
{"op":"call","id":"close-f","name":"lean_close","args":{"workspace":$WS,"path":"$F"}}
EOF
/usr/bin/time -v -o "$OUT/beam-cases.time" "$PROBE" --timeout 900 --log "$OUT/beam-cases.stderr" --rss "$OUT/beam-cases.rss" -- "$SERVER" < "$OUT/beam-cases.jsonl" | tee "$OUT/beam-cases.out.jsonl"
git checkout -q -- "$F" "$DEP" "$IMP"
echo "== rebuild the modules lean_save touched so the checkout is consistent again"
( time lake build ) 2>&1 | tail -3
echo "== stderr grep for sandbox trouble"; grep -i -c 'operation not permitted\|permission denied' "$OUT/beam-cases.stderr" || true
echo "== done; peak rss kB: $(cat "$OUT/beam-cases.rss")"
