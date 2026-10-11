# local-explore: LeanExplore as a frozen offline search service (workstream S3)

Semantic search over Mathlib + core + TauCeti at a pinned commit, served on a unix socket per pin with the
plan's wire protocol. LeanExplore's own engine (BM25 on names + FAISS on descriptions + local Qwen3
cross-encoder) and its pinned PyPI release (1.3.0); only the input and the describe stage are ours.
Verdicts a-i with file/function citations: `SOURCE-CHECKS.md`. Validation: `VALIDATION.md`. Describer pilot: `PILOT-PLAN.md`.

## Route

**Adapter.** LeanExplore's native pipeline (parse -> describe -> embed -> index) cannot pin a SHA or use an
existing checkout and would rebuild doc-gen4 per pin. Instead, S0's `decls.jsonl` is written straight into
LeanExplore's SQLite schema (`build_explore.py --stage adapt`), then:

| stage | what | cost |
|---|---|---|
| `adapt` | decls.jsonl -> `lean_explore.db` (table `declarations` from `lean_explore.models.Base`, side table `tauceti_meta`). Statement text = `<kind> <name> : <type_full>` (fallback: `signature`); `ctor` rows dropped (`--skip-kinds`). Dependencies inferred from identifiers (see open questions). | 30 s for 500k rows |
| `describe` | reuse descriptions (+ their embeddings) from other DBs (`--reuse-from`, repeatable), then LLM for the rest. Same prompt and dependency-context rules as LeanExplore. | money; guarded |
| `embed` | Qwen3-Embedding-0.6B via LeanExplore's `EmbeddingClient`, pinned revision. `--embed-fallback-text` embeds undescribed rows from name+docstring+statement (slice tests only). | CPU hours for ~124k rows (unmeasured); minutes on GPU |
| `index` | FAISS (flat below 50k vectors, else IVF like upstream) + two BM25 indexes, then `MANIFEST.json` | minutes |
| `export` | CSV for scoring descriptions | free |
| `all` | adapt, describe, embed, index | |

```bash
cd ~/lean/workboots/local-explore            # uv sync once (Python 3.14 works; .python-version pins it)
export HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES=                     # CPU while the GPU driver is broken
SEED=~/.local/share/tauceti-explore/upstream/20260714_172516       # LeanExplore prebuilt data (3.7 GB, downloaded)
.venv/bin/python build_explore.py b3-11bc13335fb9 --stage adapt
.venv/bin/python build_explore.py b3-11bc13335fb9 --stage describe --dry-run --reuse-from $SEED   # estimate, no network
OPENROUTER_API_KEY=... .venv/bin/python build_explore.py b3-11bc13335fb9 --stage describe \
    --reuse-from $SEED --informalize-model <chosen> --max-spend <usd> --i-approve-spend
.venv/bin/python build_explore.py b3-11bc13335fb9 --stage embed --device cpu     # or cuda once the driver is fixed
.venv/bin/python build_explore.py b3-11bc13335fb9 --stage index
# second pin: seed from the first (hash/name keyed; only touches rows that exist in b2)
.venv/bin/python build_explore.py b2-b5500936b563 --stage adapt
.venv/bin/python build_explore.py b2-b5500936b563 --stage describe --reuse-from ~/.local/share/tauceti-explore/b3-11bc13335fb9 --reuse-from $SEED --dry-run
```

Describe refuses to run without `OPENROUTER_API_KEY` **and** `--i-approve-spend`; the estimate is always printed first;
`--max-spend` aborts before and during the run; `--dry-run` makes no network call (it does write the free reuse into the DB).
`--base-url` / any OpenAI-compatible endpoint (vLLM) works. Reuse keys: `(name, source_text)`, then S0 `hash`, then name only
for the upstream data (`--reuse-strict` additionally requires equal docstrings); provenance per row in `tauceti_meta.description_source`.
Models and revisions: `Qwen/Qwen3-Embedding-0.6B@97b0c614be4d`, `Qwen/Qwen3-Reranker-0.6B@e61197ed4502`. The HF cache snapshots must
be complete once (online `snapshot_download(repo, revision=...)`); after that everything runs with `HF_HUB_OFFLINE=1`.
The per-pin `MANIFEST.json` carries `pin`, `tauceti_sha`, `mathlib_rev`, `lean_toolchain`, `decls_sha256` at top level (copied from the
extraction), plus models, versions, counts, sha256 of every index file. Copies go to `manifests/`.

## Service

```bash
.venv/bin/python server.py --socket-dir $XDG_RUNTIME_DIR/tauceti-explore [--port 8089] [--rerank-top 25]
./install-service.sh --pin b3-11bc13335fb9 --pin b2-b5500936b563      # writes the --user unit; does NOT enable or start it
```

One process serves every loaded pin (the embedder and reranker are shared) and listens on one socket per pin,
`<socket-dir>/<pin>/explore.sock` (mode 0666, directory 0755). A request without `pin` uses the socket's own pin; `&pin=` overrides;
TCP (`--port`) uses `--default-pin`. `/health` returns the pin's identity fields at the top of `manifest` (what the bench preflight checks).
`/search?q=&k=` (max 50) takes optional `rerank_top` (0 disables; default 25). Deterministic: float32, fixed thread count, serialised requests,
no network. `client-smoke.sh` is a curl smoke test (`TAUCETI_EXPLORE_PIN=<pin>`). The unit now serves on the GPU (`--device cuda`; the CPU-forcing `CUDA_VISIBLE_DEVICES=` and `--device cpu` were removed 2026-10-09 after the reboot).

## Cost model

Measured by `--dry-run` on the real b3 extraction (decls.jsonl 510,999 rows; 501,005 after dropping ctors) with LeanExplore's prompt
(Qwen3 tokenizer as token proxy; 110 output tokens per description assumed, upstream mean 283 chars):

* **TauCeti is 86,658 describable rows, not 38k** (70.5k theorems, 12.9k defs, 2.9k instances; the 38k was a rough grep).
* Mathlib in b3: 324,054 rows, of which **303k match the July prebuilt data by name** (reused with embeddings); only **20,646 Mathlib rows are new**.
  Core/Lake/Aesop/Std etc.: about 16.7k rows have no seed (prebuilt only had Init/Std/Lean/Batteries at v4.32).
* The seed leaves **124,008 rows to describe** (TauCeti 86,658 + Mathlib 20,646 + other 16,704) at about 1,094 input tokens each (dependency
  context included) = 135.7M input + 13.6M output tokens.

| scenario | rows | gemini-3-flash-preview (0.50/3.00) | claude-haiku-5.5 (0.10/0.50) | qwen3.8-flash (0.15/0.47) |
|---|---|---|---|---|
| seeded, everything unseeded (b3) | 124,008 | $109 (x1.5 margin $163) | $20 | $27 |
| seeded, TauCeti only (`--only-tauceti`) | 86,658 | $76 | $14 | $19 |
| no seed at all | 501,005 | about $440 | about $82 | about $108 |
| b2 after b3 (reuse by hash/name) | a few thousand | < $5 | < $1 | < $1 |

The seed is worth about 75% of the bill. Reasoning models bill thinking tokens as output; the pilot (`PILOT-PLAN.md`, about $0.28 expected) picks the model
from measured quality and cost per declaration before any of this is approved. Embedding the 124k new descriptions is free (local).

## Memory and disk (per server process)

Not measured at full scale (only a 220-row slice plus the prebuilt 409k-row data were handled); estimates: FAISS IVF 501k x 1024 x 4 B = 2.0 GB resident;
models fp32 about 2.4 GB each (embedder 0.6B, reranker 0.6B) = 4.8 GB; BM25 + id maps < 0.2 GB; SQLite is read per hit (page cache). Plan on 8 to 10 GB RSS for
one pin, about +2.5 GB per additional pin. Disk per pin about 2.3 GB db + 2 GB FAISS. Build: peak a few GB for adapt/reuse (the reuse stage held ~3 GB RSS),
IVF training on 501k x 1024 floats about 4 GB. The host has 247 GB.

## Deviations and open questions

* Per-pin sockets and top-level identity fields follow the coordinator's contract amendments (the original plan had one socket).
* Dependencies are **inferred** from identifiers in the statement against the extraction (98% of rows get >= 1 dependency, which is an over-match).
  LeanExplore reads real constants from the `name_info` type blob. Recommended: S0 emits a `deps` list (adapter already uses it when present); this
  only affects the description prompt's context block, not search.
* Reusing descriptions by name from a two-month-older Mathlib can leave a stale description when a statement changed under the same name
  (about 377k reused). `--reuse-strict` (equal docstrings) is the safer setting; statement drift is not detected for upstream data.
* Kinds: constructors are dropped; instances, `opaque`, `axiom` are kept. LeanExplore upstream dropped only `.mk`.
* Latency on an idle host and the real-index retrieval quality are unmeasured (host load 220-290 during validation).
* Embedding 124k rows on CPU: not timed; GPU fix (reboot) would make it minutes. The 220-row slice embed took 10 min under load.
* The 1.3.0 wheel lacks `prompt.txt` (our copy in `prompt.txt`); HEAD fixes it. We pin 1.3.0.
* `dev/make_sample_decls.py` and `manifests/SAMPLE-slice-*.json` are stand-ins from before S0 landed; `pilot/` holds the real selection.

## Describe persistence and spend guard (changed 2026-10-08)

`run_describe` used to persist only at the end of each dependency layer and checked `--max-spend` only between layers. Now every completed
call is persisted as it returns (row update + `describe-log.jsonl` line, flushed per line; SQLite commit every `--commit-every` rows, default 100,
and at layer end). Spend is tracked per completed call from the returned usage. At `--max-spend`, or on an OpenRouter credits/auth error (401/402/403,
"insufficient"), no new call starts, in-flight calls finish and are persisted, and the process exits with code 3. `--no-margin-check` skips the
estimate x1.5 <= `--max-spend` start-up check; use it only together with this per-row tracking (a model with no price entry is refused when `--max-spend` is set).
Rerun semantics are unchanged (todo = rows with NULL `informalization`). Tests with a stub describer (no network): `dev/test_describe_persist.py`
(cap stop with every paid row persisted, credit-error stop, SIGKILL after 17 rows leaves exactly 17 persisted and the rerun completes).
A SIGKILL loses at most `--commit-every` uncommitted rows (the real run uses 10; the first attempt, killed at 4,257 paid rows with commit-every 100, lost 57).

## Resuming the b3 describe

Output: `~/.local/share/tauceti-explore/b3-11bc13335fb9/` (`lean_explore.db`, `describe-log.jsonl`, `describe-run.log`). Adapt is already done (constructors kept, 510,999 rows);
do not rerun adapt (it would need `--force` and discard descriptions). After a reboot or a cap stop, rerun the same command (it re-applies the free strict reuse, about 5 min, then continues with rows still NULL):

```bash
cd ~/lean/workboots/local-explore
setsid nohup ./run-b3-describe.sh >> ~/.local/share/tauceti-explore/b3-11bc13335fb9/describe-run.log 2>&1 < /dev/null &
```

`run-b3-describe.sh` loads `OPENROUTER_API_KEY` from `~/.config/tauceti/openrouter_key.txt` and runs `build_explore.py b3-11bc13335fb9 --stage describe
--reuse-from ~/.local/share/tauceti-explore/upstream/20260714_172516 --reuse-strict --informalize-model anthropic/claude-haiku-5.5 --est-output-tokens 340
--max-tokens 1000 --max-spend 65 --commit-every 10 --i-approve-spend --informalize-max-concurrent 24`. `--max-spend` is cumulative: tokens already logged in `describe-log.jsonl` count toward the $65 cap, so a resume never exceeds it.

## Full build results (2026-10-09)

Host was shared with a CPU-heavy TauCetiBench smoke build, so wall-clock numbers are inflated for CPU stages (describe reuse, index, adapt).

| | b3-11bc13335fb9 | b2-b5500936b563 |
|---|---|---|
| rows (all kinds, ctors kept) | 510,999 | 473,395 |
| described / embedded | 510,862 | 473,322 |
| null (undescribed, not in FAISS, still in BM25-by-name) | 137 | 73 |
| descriptions: upstream July reuse / b3 reuse / LLM | 377,155 / - / 133,707 | 5,069 / 464,162 / 4,091 |
| index on disk | 4.9 GB (db 3.0 GB, FAISS IVF 2.1 GB) | 4.5 GB |
| embed (GPU, batch 32) | 348 s for 133,707 rows (about 385/s sustained, 0.10 GPU-h) | 133 s for 48,303 rows (0.04 GPU-h) |
| index stage (CPU, FAISS IVF + BM25) | 87 s | 72 s |
| peak VRAM during embed (whole-GPU reading incl. finder's ~17 GB resident) | 22.0 GB, i.e. about 5 GB for the embedder | 22.1 GB |

Spend (OpenRouter, claude-haiku-5.5): b3 first pass $43.98 (+ pilot $0.35), b3 retry pass $0.23 (333 rows: 196 recovered, 137 stay null: both
attempts hit `finish_reason=length` at `--max-tokens 1000`, i.e. the model ran past the cap; left null deliberately, one retry only), b2 residual $1.56
(dry-run estimate $1.23: 4,164 rows, 4,091 ok, 73 null for the same reason). Task total $1.78; build total $46.31 including the pilot.
Null-description rows are not embedded (no `--embed-fallback-text`), so they are absent from FAISS and reachable only through BM25 name matching; the reranker sees just the name for them.

Serving (one process, both pins, GPU): RSS about 7.5 GB, VRAM 9.1 GB for the process (embedder + reranker + CUDA context), p50 `/search` about 145 ms
(k=10 to 50, rerank 25, host loaded), first query after start slower (a few s). Warm start takes about 5 s.

Commands:

```bash
cd ~/lean/workboots/local-explore
./run-b3-describe.sh                          # retry pass (resume command), log in tauceti-explore/logs/b3-retry.log
export HF_HUB_OFFLINE=1
.venv/bin/python build_explore.py b3-11bc13335fb9 --stage embed --device cuda
.venv/bin/python build_explore.py b3-11bc13335fb9 --stage index
.venv/bin/python build_explore.py b2-b5500936b563 --stage adapt --skip-kinds ""        # keep constructors
.venv/bin/python build_explore.py b2-b5500936b563 --stage describe --dry-run --reuse-strict \
    --reuse-from ~/.local/share/tauceti-explore/b3-11bc13335fb9 --reuse-from ~/.local/share/tauceti-explore/upstream/20260714_172516 \
    --informalize-model anthropic/claude-haiku-5.5 --est-output-tokens 340 --max-tokens 1000
./run-b2-describe.sh                          # same flags, --max-spend 10 --i-approve-spend, key loaded inline
.venv/bin/python build_explore.py b2-b5500936b563 --stage embed --device cuda
.venv/bin/python build_explore.py b2-b5500936b563 --stage index
./install-service.sh --pin b3-11bc13335fb9 --pin b2-b5500936b563 && systemctl --user daemon-reload && systemctl --user enable --now tauceti-explore.service
TAUCETI_EXPLORE_PIN=b3-11bc13335fb9 ./client-smoke.sh; TAUCETI_EXPLORE_PIN=b2-b5500936b563 ./client-smoke.sh
```

Stage logs and helper scripts: `~/.local/share/tauceti-explore/logs/`. Per-pin `MANIFEST.json` (also copied to `manifests/`) carries the five identity fields
copied from S0, models, counts (including `null_description`, `null_embedding`) and sha256 of every index file.
