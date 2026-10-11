#!/usr/bin/env bash
# Real-agent confirmation with Claude Code INSIDE the sandbox: the MCP server comes only from
# --mcp-config (a JSON file in the run dir) with --strict-mcp-config, so nothing from a user config
# is consulted. Usage: claude.sh beam|leanlsp     (spends Max-plan quota)
set -euo pipefail
TOOL="${1:?beam|leanlsp}"
CK="$SMOKE_CHECKOUT"; TOOLS="$SMOKE_TOOLS"; OUT="$SMOKE_RUN_DIR/logs"
MODEL="${CLAUDE_MODEL:-sonnet}"
cd "$CK"
CFG="$OUT/claude-mcp-$TOOL.json"
case "$TOOL" in
  beam)
    cat > "$CFG" <<EOF
{"mcpServers":{"lean_beam":{"type":"stdio","command":"$TOOLS/beam/bin/lean-beam-mcp","args":[],"env":{"ELAN_HOME":"$ELAN_HOME","PATH":"$PATH","LANG":"C.UTF-8"}}}}
EOF
    PROMPT="Lean tools come from the lean_beam MCP server; every call needs {\"workspace\":{\"root\":\"$CK\"}} and 0-based positions. Call lean_sync on TauCeti/Analysis/PSeries.lean, then lean_goals with mode \"before\" at line 53 character 4 using the version lean_sync returned. Report the goal verbatim and stop. Do not edit files, do not run lake or any shell command."
    ;;
  leanlsp)
    cat > "$CFG" <<EOF
{"mcpServers":{"lean_lsp":{"type":"stdio","command":"$TOOLS/bin/lean-lsp-mcp","args":[],"env":{"ELAN_HOME":"$ELAN_HOME","PATH":"$PATH","LANG":"C.UTF-8","LEAN_PROJECT_PATH":"$CK","LEAN_MCP_DISABLED_TOOLS":"lean_leansearch,lean_loogle,lean_leanfinder,lean_hammer_premise","LEAN_LOG_LEVEL":"WARNING"}}}}
EOF
    PROMPT="Lean tools come from the lean_lsp MCP server (1-based positions). Call lean_goal on TauCeti/Analysis/PSeries.lean at line 54 and report the goal verbatim, then stop. Do not edit files, do not run lake or any shell command."
    ;;
  *) echo "unknown tool $TOOL" >&2; exit 2 ;;
esac
echo "== CLAUDE_CONFIG_DIR=$CLAUDE_CONFIG_DIR"; ls -la "$CLAUDE_CONFIG_DIR"; cat "$CFG"
set -x
/usr/bin/time -v -o "$OUT/claude-$TOOL.time" timeout 290 claude -p "$PROMPT" --output-format stream-json --verbose --model "$MODEL" \
  --mcp-config "$CFG" --strict-mcp-config --dangerously-skip-permissions \
  > "$OUT/claude-$TOOL.transcript.jsonl" 2> "$OUT/claude-$TOOL.stderr" || echo "claude rc=$?"
set +x
echo "== event types"; python3 - "$OUT/claude-$TOOL.transcript.jsonl" <<'EOF'
import json,sys,collections
c=collections.Counter()
for l in open(sys.argv[1]):
    l=l.strip()
    if not l: continue
    try: d=json.loads(l)
    except Exception: c['<nonjson>']+=1; continue
    t=d.get("type"); st=d.get("subtype")
    c[f"{t}/{st}" if st else t]+=1
    if t=="system" and st=="init": print("init mcp_servers:", d.get("mcp_servers"), "tools:", [x for x in d.get("tools",[]) if "mcp" in x])
    if t=="assistant":
        for b in d.get("message",{}).get("content",[]):
            if b.get("type")=="tool_use": print("tool_use:", json.dumps(b)[:600])
    if t=="user":
        for b in d.get("message",{}).get("content",[]):
            if b.get("type")=="tool_result": print("tool_result:", json.dumps(b)[:400])
    if t=="result": print("result:", json.dumps({k:d.get(k) for k in ("subtype","is_error","duration_ms","num_turns","total_cost_usd","permission_denials")}))
print(dict(c))
EOF
echo "== stderr tail"; tail -5 "$OUT/claude-$TOOL.stderr"
