# Agent prompt blocks for the LSP arms (draft)

Rendered into the fix/roadmap prompts next to the existing build instructions, one block per arm.
`{{checkout}}` is the absolute repo root inside the sandbox; `{{server}}` is the MCP server name the
harness registers (`lean_beam` / `lean_lsp`; Codex exposes tools as `<server>.<tool>`, Claude as
`mcp__<server>__<tool>`). Keep each block fixed for the whole campaign.

## Beam arm (`lean_beam`)

> Lean tools come from the `lean_beam` MCP server. Every call takes
> `{"workspace":{"root":"{{checkout}}"}}` and a `path` relative to that root. **Positions are
> 0-based** (`line`, `character` as in LSP); editor line N is `line: N-1`.
>
> Workflow:
> 1. `lean_sync` the file you are working on first. It waits until elaboration is finished and returns
>    the document `version`, diagnostic counts and `readiness` (`save_ready`). Pass that `version`
>    to every later probe on the same file. Cold sync of a Mathlib-heavy file takes 10–40 s; later
>    syncs are a few seconds.
> 2. Before editing, try candidate tactics with `lean_run_at` (`text` is one tactic block or one
>    command, run at `line`/`character` inside the proof). Several `lean_run_at` calls may be sent
>    in parallel. `lean_goals` (`mode: "before"|"after"`) shows the goal at a position, `lean_hover`
>    the type/docstring, `lean_todo` lists sorries, holes and errors in a range.
> 3. Beam never edits files: make edits with your normal file tools. After saving an edit call
>    `lean_update` (fast, returns the new `version`) or `lean_sync` (also waits for diagnostics).
>    A probe sent with an old `version` fails with `contentModified` /
>    `documentVersionMismatch`: sync again and retry, do not guess versions.
> 4. When a file is error-free (`readiness.save_ready: true`), call `lean_save` on it. That writes
>    its `.olean` so files importing it see the change without `lake build`. An importing file that is
>    already open does NOT pick the new olean up by itself: `lean_sync` on it fails with
>    `syncBarrierIncomplete` (its `recoveryPlan` says so); call `lean_refresh` on the importing
>    file, then probe it. Errors are read from `lean_sync`/`lean_refresh` with
>    `diagnostics_in_result: true` (there is no separate diagnostics tool).
> 5. `lean_run_at` results are speculative: a successful probe is not a saved edit.
> 6. Before finishing, run `lake build` once from the shell; CI builds from clean artifacts and a
>    Beam checkpoint is not a build.

## lean-lsp-mcp arm (`lean_lsp`)

> Lean tools come from the `lean_lsp` MCP server. **Positions are 1-based** (`line`, `column`), as
> in an editor. `file_path` is relative to the project root or absolute.
>
> - `lean_goal` (line, optional column) shows the goal before/after a tactic line; `lean_term_goal`,
>   `lean_hover_info`, `lean_declaration_file`, `lean_completions` as in an IDE.
> - `lean_diagnostic_messages` returns the current errors/warnings of a file. It re-reads the file
>   from disk and waits for elaboration to finish (up to a few minutes on a cold file), so call it
>   after each edit instead of `lake build`.
> - `lean_multi_attempt` tries several tactic snippets at a line in scratch copies (3+ at a time is
>   cheapest) and reports the goal and diagnostics of each; nothing is written to your file.
> - `lean_run_code` compiles a self-contained snippet with its own imports.
> - `lean_build` runs `lake build` for the project; prefer the shell `lake build` at the end.
> - There is no checkpoint/save: files importing a changed module see the change only after
>   `lake build`. Search tools (LeanSearch, Loogle, Lean Finder, hammer) are disabled in this run.
> - Before finishing, run `lake build` once from the shell.

## Shared

- The sandbox has no network: tools that would call a web service fail or are absent; do not retry them.
- Timeouts: a single tool call may take up to 10 minutes (`tool_timeout_sec=600`); a cold first
  call on a Mathlib-heavy file is the slow one, later calls on the same file are fast.
