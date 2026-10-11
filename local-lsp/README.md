# local-lsp: Lean MCP servers for the TauCetiBench LSP arms (S4a)

Smoke-test kit and pinned installs for **Lean Beam** (`lean_beam`) and **lean-lsp-mcp** (`lean_lsp`)
on the two corpus toolchains, run inside a bubblewrap sandbox shaped like TauCetiBench's.
Results and the go/no-go table: `SMOKE-RESULTS.md`. Prompt blocks: `agent-notes.md`.

## Files

| File | Purpose |
|---|---|
| `install-beam.sh` | Clone `leanprover/lean-beam` at the pinned commit under `~/.local/opt/tauceti-tools/beam/src`, run its installer with `BEAM_BIN_HOME`/`BEAM_INSTALL_ROOT` under the same tree, prebuild the plugin bundle for v4.34.0-rc2 and v4.35.0-rc3. No sudo, nothing under `/opt`, no agent skills, no MCP client registration. |
| `install-lean-lsp-mcp.sh` | `uv tool install lean-lsp-mcp==<pin>` on `/usr/bin/python3` into `~/.local/opt/tauceti-tools/{uv-tools,bin}`. |
| `write-manifest.sh` | Writes `~/.local/opt/tauceti-tools/MANIFEST.json` (pins, toolchains, interpreter, sha256 of entry points). |
| `smoke-sandbox.sh` | Runs a command inside a bwrap sandbox with exactly the bench's shape (see below). |
| `mcp_probe.py` | Stdio MCP JSON-RPC client driven by a JSONL script: `initialize`, `tools/list`, sequential and parallel `tools/call`, shell steps, timings, peak RSS of the server tree. No model involved. |
| `cases/leanlsp.sh`, `cases/beam.sh` | The probe scripts per tool (run inside the sandbox). |
| `cases/codex.sh`, `cases/claude.sh` | One short real-agent run per provider with the server injected at the argv level. |

## Reinstall at the pins

```bash
~/lean/workboots/local-lsp/install-beam.sh            # BEAM_PIN=4b045750bd7ba359abc881145fcfa9ca9323f79c; ~4 min wall / 8 CPU-min
~/lean/workboots/local-lsp/install-lean-lsp-mcp.sh    # LEAN_LSP_MCP_VERSION=0.31.0
~/lean/workboots/local-lsp/write-manifest.sh
```

Pins: Lean Beam `4b045750bd7ba359abc881145fcfa9ca9323f79c` (main, 2026-10-08, `lean-beam 0.2.0-beta`;
Beam itself builds with `leanprover/lean4:v4.35.0-rc4`, which elan fetches once); lean-lsp-mcp
`0.31.0` (PyPI 2026-09-30; deps `leanclient 0.13.2`, `mcp 2.0.0`) on `/usr/bin/python3` = 3.14.4.

Neither corpus toolchain is in Beam's `validated-lean-toolchains` at this commit (it lists
v4.34.0-rc1, v4.34.0, v4.34.1, v4.35.0-rc4); both are admitted through
`compatible-lean-release-lines` (`v4.34`, `v4.35`) and the installer prebuilds a bundle for each
(`lean-beam doctor` → `project toolchain admission: release-line`, `bundle source: installed`).
A future toolchain bump needs `install-beam.sh` re-run with the new `--toolchain` (same commit is
fine while the release line is listed).

## Smoke checkouts

`~/.local/share/tauceti-lsp-smoke/{rc2,rc3}/TauCeti`, cloned with `--reference
/srv/tauceti/records/git/TauCeti.git`:

- rc3: `11bc13335fb906290a0bcbe57747f20cd21dbf2c` (the b3 pin), Mathlib `5e0c4e5239cb…`, v4.35.0-rc3
- rc2: `8befae0657614ef00266488439385114d1a2d4ab` (newest `main` commit still on v4.34.0-rc2,
  2026-09-25; the next commit `4110400b` bumps to v4.34.0), Mathlib `dc4b8d60d5ed…`

Built with `lake exe cache get` then `lake build` (the TauCeti Lake cache `lake cache get --service
tauceti-public` needs a user-level Lake service config this host lacks; a from-scratch TauCeti build
is ~15 min on 128 cores, so it was not needed).

## What the bench must bind for the LSP arms

In addition to `sandbox.py`'s base (tmpfs `/`, ro `/usr` `/etc`, fresh `/dev` `/proc` `/tmp`) and the
runner's existing mounts (repo rw, pool `.lake` rw, `LAKE_CACHE_DIR`/`MATHLIB_CACHE_DIR` rw, run dir
rw, bin dir at `/opt/tauceti-bench/bin`, `ELAN_HOME` ro, context ro, credentials):

| Bind | Mode | Why |
|---|---|---|
| `~/.local/opt/tauceti-tools` | ro | both servers: `beam/bin/*` wrappers, `beam/share/versions/<payload>/libexec/*`, `beam/share/state/install-bundles/linux-x86_64/<id>/workspace/.lake/build/{bin/beam-daemon,lib/libbeam_Beam_LSP.so}`, `uv-tools/lean-lsp-mcp` venv, `bin/lean-lsp-mcp` |
| `~/.elan` | ro | already bound as `ELAN_HOME`; Beam resolves `lean`/`lake` from the project's `lean-toolchain` through it |

For the agent step itself (Codex/Claude reaching their API) the bench's sandbox has network on; the
smoke runs use `smoke-sandbox.sh --net` for those, `--unshare-net` (default) for the probe cases. The
servers need no network. Checkouts must be self-contained (no `objects/info/alternates` pointing
outside the binds): a `git checkout` inside the sandbox on a `--reference` clone fails with
"unable to normalize alternate object path" and deletes the file; the smoke checkouts were repacked
(`git repack -a -d`, alternates removed). If the bench's repo clones use alternates, the agent's own
git commands are affected the same way, independent of this work.

Nothing else: lean-lsp-mcp's venv points at `/usr/bin/python3.14` (`home = /usr/bin` in `pyvenv.cfg`),
so no `~/.local/share/uv` python is needed. Beam writes only under the project (`.beam/`,
`.lake/build` on `lean_save`) and `$HOME`-free paths; lean-lsp-mcp writes nothing outside the project.

Environment the servers need (the bench already sets these for the agent): `PATH` containing
`$ELAN_HOME/bin`, `ELAN_HOME`, `HOME` (writable), `LANG`, `TMPDIR`; `LAKE_*`/`MATHLIB_CACHE_DIR` as
for `lake build`. **Codex starts stdio MCP servers with a minimal env** (HOME, PATH, TMPDIR, LANG,
USER, SHELL, TERM, …) so `ELAN_HOME` and the `LAKE_*` variables must be whitelisted with
`mcp_servers.<id>.env_vars` or set with `mcp_servers.<id>.env`. Claude Code forwards the parent env
plus the `env` map of the server entry.

## Running the smoke tests

```bash
./smoke-sandbox.sh --toolchain rc3 -- bash /opt/tauceti-bench/bin/cases/leanlsp.sh
./smoke-sandbox.sh --toolchain rc3 -- bash /opt/tauceti-bench/bin/cases/beam.sh
./smoke-sandbox.sh --toolchain rc3 --codex-auth  -- bash /opt/tauceti-bench/bin/cases/codex.sh beam
./smoke-sandbox.sh --toolchain rc3 --claude-auth -- bash /opt/tauceti-bench/bin/cases/claude.sh beam
```

Each run gets `~/.local/share/tauceti-lsp-smoke/runs/<stamp>/` (or `--run-dir`) holding `home/`,
`codex/`, `claude/`, `bin/` and `logs/` with the probe output (`*.out.jsonl`), server stderr,
`/usr/bin/time -v` records and peak RSS.
