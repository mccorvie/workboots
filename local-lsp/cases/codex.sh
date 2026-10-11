#!/usr/bin/env bash
# Real-agent confirmation: one short `codex exec` turn INSIDE the sandbox with the MCP server
# supplied purely through -c overrides (no config.toml content). Usage: codex.sh beam|leanlsp
# Spends subscription quota: keep to a handful of runs.
set -euo pipefail
TOOL="${1:?beam|leanlsp}"
CK="$SMOKE_CHECKOUT"; TOOLS="$SMOKE_TOOLS"; OUT="$SMOKE_RUN_DIR/logs"
MODEL="${CODEX_MODEL:-gpt-5.6-sol}"
cd "$CK"
# Codex starts stdio MCP servers with a minimal environment (HOME, PATH, TMPDIR, LANG, ... but not
# ELAN_HOME or the LAKE_* variables), so whitelist what lake/elan need with env_vars.
ENV_VARS='["ELAN_HOME","LANG","TMPDIR","LAKE_CACHE_DIR","MATHLIB_CACHE_DIR","LAKE_ARTIFACT_CACHE","LAKE_RESTORE_ARTIFACTS"]'
case "$TOOL" in
  beam)
    OVERRIDES=(
      -c "mcp_servers.lean_beam.command=\"$TOOLS/beam/bin/lean-beam-mcp\""
      -c "mcp_servers.lean_beam.startup_timeout_sec=60"
      -c "mcp_servers.lean_beam.tool_timeout_sec=600"
      -c "mcp_servers.lean_beam.required=true"
      -c "mcp_servers.lean_beam.supports_parallel_tool_calls=true"
      -c "mcp_servers.lean_beam.env_vars=$ENV_VARS"
    )
    PROMPT="Lean tools come from the lean_beam MCP server; every call needs {\"workspace\":{\"root\":\"$CK\"}} and 0-based positions. Call lean_sync on TauCeti/Analysis/PSeries.lean, then lean_goals with mode \"before\" at line 53 character 4 (0-based; that is 1-based line 54 '    nlinarith [...]') using the version lean_sync returned. Report the goal verbatim and stop. Do not edit files, do not run lake or any shell command."
    ;;
  leanlsp)
    OVERRIDES=(
      -c "mcp_servers.lean_lsp.command=\"$TOOLS/bin/lean-lsp-mcp\""
      -c "mcp_servers.lean_lsp.startup_timeout_sec=60"
      -c "mcp_servers.lean_lsp.tool_timeout_sec=600"
      -c "mcp_servers.lean_lsp.required=true"
      -c "mcp_servers.lean_lsp.env={LEAN_PROJECT_PATH=\"$CK\",LEAN_MCP_DISABLED_TOOLS=\"lean_leansearch,lean_loogle,lean_leanfinder,lean_hammer_premise\",LEAN_LOG_LEVEL=\"WARNING\"}"
      -c "mcp_servers.lean_lsp.env_vars=$ENV_VARS"
    )
    PROMPT="Lean tools come from the lean_lsp MCP server (1-based positions). Call lean_goal on TauCeti/Analysis/PSeries.lean at line 54 and report the goal verbatim, then stop. Do not edit files, do not run lake or any shell command."
    ;;
  *) echo "unknown tool $TOOL" >&2; exit 2 ;;
esac
echo "== CODEX_HOME=$CODEX_HOME"; ls -la "$CODEX_HOME"; cat "$CODEX_HOME/config.toml"
set -x
/usr/bin/time -v -o "$OUT/codex-$TOOL.time" timeout 290 codex exec --json --model "$MODEL" \
  -c 'model_reasoning_summary="detailed"' -c show_raw_agent_reasoning=false \
  --sandbox danger-full-access --skip-git-repo-check "${OVERRIDES[@]}" "$PROMPT" \
  > "$OUT/codex-$TOOL.transcript.jsonl" 2> "$OUT/codex-$TOOL.stderr" || echo "codex rc=$?"
set +x
echo "== event types"; python3 - "$OUT/codex-$TOOL.transcript.jsonl" <<'EOF'
import json,sys,collections
c=collections.Counter()
for l in open(sys.argv[1]):
    l=l.strip()
    if not l: continue
    try: d=json.loads(l)
    except Exception: c['<nonjson>']+=1; continue
    t=d.get("type"); it=d.get("item",{}).get("type") if isinstance(d.get("item"),dict) else None
    c[f"{t}/{it}" if it else t]+=1
    if it and 'mcp' in str(it): print(json.dumps(d)[:800])
    if t and 'mcp' in str(t): print(json.dumps(d)[:800])
print(dict(c))
EOF
echo "== stderr tail"; tail -5 "$OUT/codex-$TOOL.stderr"
echo "== session files under CODEX_HOME"; find "$CODEX_HOME" -maxdepth 3 -type f | head -20
