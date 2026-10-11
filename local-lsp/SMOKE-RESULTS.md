# S4a smoke results: Lean Beam vs lean-lsp-mcp (2026-10-08/09)

Host `martingale`, 128 cores. Everything below ran inside `smoke-sandbox.sh` (tmpfs `/`, ro `/usr` `/etc`,
fresh `/dev` `/proc` `/tmp`, `--clearenv`, all namespaces unshared, `--disable-userns`; `--unshare-net`
except for the three agent runs, which need their model API). Raw outputs: `~/.local/share/tauceti-lsp-smoke/runs/`
(`rc{2,3}-{beam,leanlsp}`, `rc3-codex-{beam,leanlsp}`, `rc3-claude-beam`). No sandbox "operation not
permitted"/"permission denied" appeared in any server stderr.

Pins: Beam `4b045750bd7ba359abc881145fcfa9ca9323f79c` (`lean-beam 0.2.0-beta`); lean-lsp-mcp `0.31.0`
(leanclient 0.13.2, mcp 2.0.0) on `/usr/bin/python3.14`. Neither toolchain is in Beam's
`validated-lean-toolchains`; both are admitted by release line (`doctor`: `admission: release-line`,
`accepted: true`, `bundle source: installed`). Test files: `TauCeti/Analysis/PSeries.lean` (imports Mathlib),
`TauCeti/MeasureTheory/Measure/Measurability.lean` (importee) and `.../Probability/Distributions/Poisson/Measurability.lean` (importer).
Checkouts: rc2 `8befae06`, rc3 `11bc1333`; `lake build` completes on both (host check, 20-30 s no-op).

## Decision table

| Check | Beam rc2 | Beam rc3 | lean-lsp-mcp rc2 | lean-lsp-mcp rc3 |
|---|---|---|---|---|
| host self-check (`doctor` / `--version` / `lake build`) | GO | GO | GO | GO |
| `tools/list` in sandbox | GO, 24 tools | GO, 24 | GO, 22 (18 with remote search off) | same |
| cold first call on Mathlib-heavy file | `lean_sync` 15.6 s | `lean_sync` 12.8-13.4 s | `lean_goal` 12.4 s | `lean_goal` 23.3 s (first run; page cache) |
| warm call | sync 3 ms, goals 8-13 ms | sync 6 ms, goals 16 ms | goal 20 ms | goal 25 ms |
| 4 concurrent `lean_run_at` | GO, wall 294 ms | GO, wall 408 ms | n/a (2 parallel `lean_multi_attempt`: 1.2 s / 1.5 s wall, GO) | same |
| `lean_save` visible to dependent | GO via `lean_refresh` (save 5.1 s) | GO via `lean_refresh` (save 7.3 s) | no such feature (`lean_build` only) | same |
| stale version / `contentModified` | GO: `contentModified`/`documentVersionMismatch` with `expectedVersion`, `currentVersion` | GO | n/a (no versions) | n/a |
| 0-based (Beam) / 1-based (lsp) positions | confirmed | confirmed | confirmed | confirmed |
| diagnostics after edit | `lean_sync` waits (cold 13 s, warm edit 270-310 ms) | same | `lean_diagnostic_messages` re-reads disk and waits: 132 ms (rc2), 154 ms (rc3) for a one-line append, error returned, `partial:false` | same |
| `lean_multi_attempt` 3 tactics | n/a (`lean_run_at` x N) | n/a | GO, 11.7 s cold | GO, 17.2 s cold; 3 snippets, per-snippet diagnostics |
| sandbox "operation not permitted" | none | none | none | none |
| peak RSS, whole server tree (probe sampler) | 6.69 GB | 6.62 GB (7.02 GB first run) | 5.14 GB | 5.27 GB |
| peak RSS, largest single process (`time -v`) | 1.66 GB | 1.63 GB | 1.08 GB | 1.09 GB |
| server start (initialize) | 130 ms | 140 ms | 1.0-1.7 s | 1.1-1.7 s |
| whole case script wall | 48 s | 52-58 s | 41 s | 69 s |
| real agent, Codex | not run | GO | not run | GO |
| real agent, Claude | not run | GO | not run | not run (skipped) |

Verdict: both GO on both toolchains. RSS is the dominant cost (about 5-7 GB of Lean processes per
agent when the file imports Mathlib; budget it against concurrency). Lean-lsp-mcp holds the per-file
elaboration in one `lake serve`-like worker tree; Beam runs a daemon plus a Lean worker per open file
(importer + importee both open = two workers, hence the higher total).

## Behaviour notes

- Beam `lean_save` writes `.olean/.ilean/.ir/.c/.trace` under `.lake/build`. An already-open importer
  then fails `lean_sync` with `syncBarrierIncomplete` (`staleDirectDeps`, `recoveryPlan`: `lean-beam refresh <path>`,
  `lake build`); `lean_refresh` on the importer (9.6-12.7 s) fixes it and the new lemma is then visible to `lean_run_at`.
  Saves dirty `.lake/build`, so a final `lake build` is still needed.
- Beam picks up on-disk edits at the next call: a probe with the old version after an external edit
  gets `contentModified` (document already at version 2); `lean_update`/`lean_sync` return version 2.
  `lean_update` returned `changed:false` because the failed probe had already synced the change.
- Beam has no diagnostics tool: use `lean_sync`/`lean_refresh` with `diagnostics_in_result:true`;
  `lean_todo` lists sorries/holes with the tactic state at each.
- `#check` through `lean_run_at` works but prints the Mathlib `linter.hashCommand` message (harmless).
- lean-lsp-mcp `lean_build` ran `lake build` in 14-24 s (no-op build). `lean_hover_info`, `lean_goal` OK.
- Remote tools (`lean_leansearch`, `lean_loogle`, `lean_leanfinder`, `lean_hammer_premise`) are listed
  by default and would fail without network. `LEAN_MCP_DISABLED_TOOLS=lean_leansearch,lean_loogle,lean_leanfinder,lean_hammer_premise`
  removes exactly those four (22 -> 18, verified in `leanlsp-list-disabled.jsonl`).
  `lean_local_search` (ripgrep, local) stays; `lean_verify`, `lean_run_code`, `lean_profile_proof`, widgets are local.
- Do not `git checkout` inside the sandbox on a `--reference` clone (alternates outside the binds): it fails and
  leaves the file deleted. Smoke checkouts were repacked standalone.

### Full tool names

Beam (`lean_beam`, 24): `beam_version beam_stats beam_feedback_report lean_drop_workspace lean_run_at lean_run_at_handle lean_hover lean_signature_help lean_definition lean_references lean_document_symbols lean_workspace_symbols lean_goals lean_todo lean_code_action_resolve lean_run_with lean_run_with_linear lean_release lean_update lean_sync lean_refresh lean_save lean_close_save lean_close`.

lean-lsp-mcp (`lean_lsp`, 22 default): `lean_build lean_file_outline lean_diagnostic_messages lean_code_actions lean_goal lean_term_goal lean_hover_info lean_completions lean_declaration_file lean_references lean_local_search lean_leansearch lean_loogle lean_leanfinder lean_hammer_premise lean_multi_attempt lean_run_code lean_verify lean_minimal_hypotheses lean_profile_proof lean_get_widgets lean_get_widget_source` (last four of the remote group removed by the env var above).

## Real-agent confirmation (3 runs: 2 Codex, 1 Claude)

CODEX_HOME = fresh run dir with a one-line comment `config.toml` (+ bind-mounted `auth.json`), `--sandbox danger-full-access`.
Both Codex runs listed and called the tool with no approval prompt (`required=true` would have aborted otherwise).
Wall: Codex+beam 23 s (2 calls, 48k+14k tokens), Codex+lean_lsp 46 s (1 call), Claude+beam 20 s (4 turns, $0.063).

Codex override set that worked (Beam; lean_lsp swaps the command and adds `env`):

```
-c 'mcp_servers.lean_beam.command="/home/firstone/.local/opt/tauceti-tools/beam/bin/lean-beam-mcp"'
-c mcp_servers.lean_beam.startup_timeout_sec=60
-c mcp_servers.lean_beam.tool_timeout_sec=600
-c mcp_servers.lean_beam.required=true
-c mcp_servers.lean_beam.supports_parallel_tool_calls=true
-c 'mcp_servers.lean_beam.env_vars=["ELAN_HOME","LANG","TMPDIR","LAKE_CACHE_DIR","MATHLIB_CACHE_DIR","LAKE_ARTIFACT_CACHE","LAKE_RESTORE_ARTIFACTS"]'

-c 'mcp_servers.lean_lsp.command="/home/firstone/.local/opt/tauceti-tools/bin/lean-lsp-mcp"'
-c 'mcp_servers.lean_lsp.env={LEAN_PROJECT_PATH="<checkout>",LEAN_MCP_DISABLED_TOOLS="lean_leansearch,lean_loogle,lean_leanfinder,lean_hammer_premise",LEAN_LOG_LEVEL="WARNING"}'
-c 'mcp_servers.lean_lsp.env_vars=[...same list...]'   (+ startup/tool timeout, required)
```

`env_vars` is essential: Codex gives stdio servers a minimal env, so `ELAN_HOME` and `LAKE_*` would be lost.
Codex lists the lean_lsp tools lazily (the agent did a "dynamic tool discovery" reasoning step first) but called without prompt.

Codex `--json` MCP event shapes (one item id, two events; arguments are exactly what the model sent):

```
{"type":"item.started","item":{"id":"item_3","type":"mcp_tool_call","server":"lean_beam","tool":"lean_sync","arguments":{...},"result":null,"error":null,"status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_3","type":"mcp_tool_call","server":"lean_beam","tool":"lean_sync","arguments":{...},"result":{"content":[{"type":"text","text":"<json string>"}],"structured_content":{...}},"error":null,"status":"completed"}}
```

Other event types seen: `thread.started`, `turn.started`, `item.completed` with `agent_message` / `reasoning`, `turn.completed` (with `usage`).
Server name is `lean_beam` / `lean_lsp`, tool names are bare (`lean_sync`), no prefix.

Claude (worked, `--dangerously-skip-permissions`, no prompt): `claude -p "$PROMPT" --output-format stream-json --verbose --model sonnet --mcp-config run/logs/claude-mcp-beam.json --strict-mcp-config --dangerously-skip-permissions`

```json
{"mcpServers":{"lean_beam":{"type":"stdio","command":"/home/firstone/.local/opt/tauceti-tools/beam/bin/lean-beam-mcp","args":[],"env":{"ELAN_HOME":"/home/firstone/.elan","PATH":"<sandbox PATH>","LANG":"C.UTF-8"}}}}
```

Events: `system/init` has `mcp_servers:[{"name":"lean_beam","status":"connected"}]` and tool names `mcp__lean_beam__<tool>`;
MCP tools are deferred, so the model first issues a `ToolSearch` `tool_use` (`select:mcp__lean_beam__lean_sync,...`) and then
`tool_use` blocks named `mcp__lean_beam__lean_sync` (input has no server prefix); results arrive as `user` events with a `tool_result` block whose `content` is the JSON string.
`result/success` has `permission_denials: []`. The call extractor must count `ToolSearch` uses separately and match `mcp__<server>__*` names.

## Recommendation

- Primary: **Lean Beam** (`lean_beam`). Versioned probes make stale-state failures explicit, `lean_run_at` gives speculative
  tactic trials with real concurrency (4 parallel in 0.3-0.4 s), and `lean_save`+`lean_refresh` avoids `lake build` on dependents.
  Cost: higher RSS (about 6.6 GB vs 5.2 GB) and a more intricate protocol (versions, workspace argument, refresh after save);
  it is on release-line admission only, not a validated toolchain, so re-run this smoke when toolchains bump.
- Comparison/fallback: **lean-lsp-mcp** (`lean_lsp`): simpler (file path + 1-based line), `lean_diagnostic_messages` and
  `lean_multi_attempt` work out of the box, lower RSS, no save/checkpoint. Run it with the four remote tools disabled.
- Both are GO on rc2 and rc3; run them as separate arms, never both in one agent.

## Open questions

1. Does the bench's repo clone use git alternates? If so, in-sandbox git needs the alternates path bound (or repack); orthogonal to the tools.
2. Memory budget per concurrent agent (5-7 GB Lean tree, peak) against the host's concurrency limit.
3. Claude run only covered Beam on rc3; lean_lsp under Claude and rc2 under either agent were not run (budget of 3).
4. Beam `lean_save` mutates `.lake/build`; pool `.lake` is shared in the bench, so confirm whether saves from one batch may leak to another (use per-run `.lake` or ignore saved oleans).
