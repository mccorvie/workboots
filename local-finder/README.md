# tauceti-finder: Lean Finder semantic search for TauCetiBench arms

Index builder and unix-socket server for [Lean Finder](https://github.com/delta-lab-ai/lean-finder) (an 8B Qwen3-based
embedding model, `delta-lab-ai/lean-finder`), serving the finder wire protocol of `workboots-plan.md`. It consumes the shared
`decls.jsonl` extraction (`~/.local/share/tauceti-index/<pin>/`) and embeds each declaration with Lean Finder's own entry
formatting and pooling. Status: both pins (b3, b2) built on the GPU and served by the `tauceti-finder` systemd --user unit (see "GPU build results").

## Layout

| path | what |
|---|---|
| `build_finder.py` | `build_finder.py <pin> [--device cuda:0\|cpu] [--limit N] [--stride S] [--dry-run] [--decls FILE]` |
| `server.py` | `GET /health`, `GET /search?q=&k=&pin=`; `--unix-socket PATH` and/or `--port N` |
| `finder_common.py` | shared pieces: `decls` -> Lean Finder row adapter, `Embedder` (their load/tokenize/pool/normalise sequence), embedding cache |
| `fetch_model.py`, `download_corpus.py` | pinned model snapshot; upstream corpus + index (for format checks only) |
| `scripts/dump_typefull.{lean,sh}`, `scripts/merge_typefull.py` | exact `typeFull` via `ppExpr` (see FORMAT-CHECK.md; strongly recommended) |
| `scripts/upstream_to_decls.py`, `scripts/check_against_upstream.py` | CPU validation helpers |
| `vendor/lean_finder/` | upstream code at the pinned commit, unmodified (`MANIFEST` lists sha256s) |
| `install-service.sh`, `systemd/tauceti-finder.service`, `client-smoke.sh` | `systemd --user` unit (installs only; **not enabled or started**) and a smoke client |
| `FORMAT-CHECK.md`, `VALIDATION.md` | how `typeFull` is rendered upstream; CPU validation numbers |

## Pins

- lean-finder `69b764614453057013e411a8c8081f447cda64e7` (2026-05-21, "update link").
- Model `delta-lab-ai/lean-finder` @ `a9e312299c4d68f020662a68d70c4e3979781f91` (`main` as of 2026-06-04, not the paper's `old-model` branch), 16 GB safetensors,
  stored in `~/.local/share/tauceti-finder/models/lean-finder-a9e312299c4d`.
- torch 2.9.0+cu128, transformers 4.57.5, faiss-cpu 1.13.2, Python 3.12 (`uv.lock`). Embedding dimension 4096 (Qwen3 hidden size).
- Every `MANIFEST.json` records these, plus sha256 and size of `corpus.jsonl` and `corpus.index`, the decls sha256, device, dtype, timings.

## What is embedded

`format_entry` from upstream `build_index.py`, imported not copied:
`{'formal_name': '<name>', 'kind': '<kind>', 'type': '<typeFull>', 'docstring': '<docString>'}`; missing docstring -> `None`. Passage: left padding, last-token pooling,
L2-normalised fp32, max length 8192, FAISS `IndexFlatIP`. Query: the upstream `get_detailed_instruct` prefix, same tokenizer/pooling/normalisation, exact inner-product search, no reranking
(deterministic). Embeddings are cached under `~/.local/share/tauceti-finder/cache/<model-rev>/<cuda|cpu>-<dtype>/` keyed by sha256 of the formatted entry, so rebuilding for another pin
only embeds declarations whose text changed.

## Build a pin

```
cd ~/lean/workboots/local-finder
uv sync                                         # once
.venv/bin/python fetch_model.py                 # once; resumes
# 1. exact typeFull for every declaration (CPU, single thread, ~10 min): needs the pin's built scratch checkout
scripts/dump_typefull.sh b3-11bc13335fb9        # writes decls.typefull.jsonl next to decls.jsonl
# 2. estimate, then build (GPU)
.venv/bin/python build_finder.py b3-11bc13335fb9 --decls ~/.local/share/tauceti-index/b3-11bc13335fb9/decls.typefull.jsonl --dry-run
.venv/bin/python build_finder.py b3-11bc13335fb9 --decls ~/.local/share/tauceti-index/b3-11bc13335fb9/decls.typefull.jsonl --device cuda:0
```

Output: `~/.local/share/tauceti-finder/<pin>/{corpus.jsonl,corpus.index,MANIFEST.json}` plus a copy of the manifest in `./manifests/<pin>.json` (repo-tracked copy, per the plan).
With `--limit`/`--stride` output goes to `.../slices/<pin>-<device>-...` instead, so a slice never overwrites a real index. Without `--decls` the builder reads
`decls.jsonl` and rebuilds `typeFull` from doc-gen4 `signature`, which matches Lean Finder's text only ~16% of the time (FORMAT-CHECK.md); it warns and counts the rows in the manifest
(`type_full_source`). Do not use that fallback for the benchmark index.

## Run the service

```
.venv/bin/python server.py --pin b3-11bc13335fb9 --pin b2-b5500936b563 --socket-root "$XDG_RUNTIME_DIR/tauceti-finder"
./client-smoke.sh --socket "$XDG_RUNTIME_DIR/tauceti-finder/b3-11bc13335fb9/finder.sock"
```

`--socket-root` binds one socket per pin at `<root>/<pin>/finder.sock`; a request on it defaults to that pin and `/health` returns the pin's identity fields (`pin`, `tauceti_sha`, `mathlib_rev`, `lean_toolchain`, `decls_sha256`) at the top level of `manifest` next to the service fields (the `pins` map is kept). The socket directories are mode 0755 and the socket 0666 (mirroring Loogle); `--port N` adds an optional loopback TCP listener for host runs. The model loads once, all pins' indexes
load at startup (sha256 verified against the manifests unless `--no-verify`), and the sockets bind only after that, so a reachable socket means ready. `GET /search?q=&k=10` uses the default pin;
`&pin=` selects another configured pin (the bench should pass the batch's pin explicitly). Errors are 4xx/5xx `{"error": ...}`.
`x-tauceti-*` request headers are logged per search. HF access is forced offline.

For `systemd --user`: `./install-service.sh` writes the unit and `~/.config/tauceti-finder/finder.env` (pins, device) and does **not** enable or start it; `--enable` does. The unit
allows 30 min to start (weights plus indexes).

## Resources

| | |
|---|---|
| Index size | 4096 floats x 4 B = 16,384 B per row: 350k rows = **5.7 GB** per pin (420k rows = 6.9 GB), held in RAM by the server; plus `corpus.jsonl` (~0.3 GB) |
| Server on GPU | bf16 weights 16 GB VRAM + a few GB activations for short queries; ~6 GB RAM per pin |
| Server on CPU | fp32 weights ~32 GB RAM (the host has 247 GB), ~2-20 s per query on a loaded 128-thread host (VALIDATION.md); fine for a smoke run, not for a campaign |
| Build on GPU | model load ~1 min; bf16; batches of up to 32 rows / 32,768 padded tokens, length-sorted so padding is small |
| Build on CPU | 31.5 tokens/s measured under heavy load: 350k rows x ~125 tokens = 44M tokens = ~16 days. Not an option |

### GPU build time estimate (not measured; GPU is down)

Mean passage length is 125 tokens for upstream Mathlib rows (5,000-row sample, null docstrings, p95 260, max 776; a TauCeti or core row with a docstring is longer), 84 tokens on the
`ppExpr`-typed core-Lean sample (no docstrings). Take 125-175 tokens x 350k rows = 44-61M tokens. At an assumed 6,000 tokens/s (bf16, one RTX Pro 6000; the prior `--dry-run` uses)
that is **2-3 hours**; by FLOPs (2 x 8B x 61M = 1e18 at 35-40% of ~500 TFLOP/s dense bf16) it is ~1.5 hours, so plan for 1.5-3 hours per full index. The first GPU build records the
real rate in `MANIFEST.json["timing"]`; the embedding cache makes the second pin (b2 vs b3 share most declarations) much cheaper. `build_finder.py <pin> --dry-run --tokens-per-sec R` recomputes the estimate.
The row count is not known until S0 delivers: Mathlib alone is ~313k declarations by the plan, TauCeti ~38k, and the extraction keeps core Lean, structures, constructors and projections too
(core Init/Std alone is ~67k rows), so the real count may be ~400-450k.

## Deviations from the contract

- `/health` on the legacy single socket / TCP without `pin` returns a service-level manifest (`manifest.pins.<pin>` holds each `MANIFEST.json`); `/health?pin=` returns that pin's manifest directly. On a per-pin socket the identity fields are at the top level (contract amendment).
- Extra optional query parameter `pin`; `kind` in hits is the extraction's own kind (`theorem`, `def`, `instance`, `structure`, `ctor`, ...) rather than Lean Finder's vocabulary
  (the embedded kind is the normalised one).
- `description` is always `""` (Lean Finder has no informal text in our rows).
- `signature` in hits is the `typeFull` that was embedded (`ppExpr` type), not the doc-gen4 header.

## Open questions

1. **`typeFull` source.** Run `scripts/dump_typefull.sh` as a stage of S0 (it needs the pin's built scratch checkout and ~10 CPU-min), or add the dump to `extract.py`? Without it the index text
   differs from what the model was trained on in most rows (the signature rebuild matched exactly 16% on core Lean).
2. **Kinds.** Upstream's corpus has no structures, classes, constructors, projections or Lean core/Std. Keep them (wider recall, off-distribution `kind` strings) or filter to
   theorem/def/instance/inductive/opaque from Mathlib+TauCeti? Default: keep.
3. **Private and auxiliary names.** The extraction drops `_private.` names; `dump_typefull` skips internal names (`isInternal`). Upstream additionally holds some `.aux`/`match_` rows (about 400);
   no action taken.
4. **Docstrings.** Embedded in full, as upstream. Very long docstrings are truncated at 8192 tokens by the tokenizer (upstream behaviour).
5. **One service for both pins, or one per pin.** The service can load b2 and b3 together (11-14 GB of index RAM); the bench must send the right `pin`. If you prefer one service per pin, run two units with different `TAUCETI_FINDER_PINS`
   and sockets (the unit hard-codes one socket path today).
6. **GPU serving.** Serving on the GPU keeps 16 GB resident while the benchmark runs; CPU serving costs seconds per query. Decide before the campaign.

## GPU build results (2026-10-08/09)

Host: one RTX PRO 6000 Blackwell (96 GB). Concurrent CPU/disk jobs (bench smoke build, LeanExplore describe) were running, which inflates wall-clock figures; the GPU was otherwise idle.

| | b3-11bc13335fb9 | b2-b5500936b563 |
|---|---|---|
| rows embedded | 510,999 (51 `type_full` nulls from `signature`) | 473,395 (463,367 cache hits from b3, 10,028 embedded) |
| tokens embedded | 67.4 M | 1.7 M |
| wall time (whole script) | 1 h 07 m 34 s (embed 3,983 s, 16.9k tok/s, 128 rows/s) | 2 m 27 s (embed 104 s) |
| GPU-hours | 1.13 | 0.04 |
| peak VRAM (nvidia-smi, whole GPU) | 33.9 GB incl. ~1.7 GB of other use (about 32 GB net) | similar, not separately sampled |
| builder peak RSS | 31.9 GB | 28.3 GB |
| index on disk | 8.3 GB (`corpus.index` 8.37e9 B, `corpus.jsonl` 0.51e9 B) | 7.7 GB |

Throughput was 15-17k tokens/s, about 2.8x the 6k prior; the embedding cache made the second pin nearly free. Manifests: `~/.local/share/tauceti-finder/<pin>/MANIFEST.json`, copies in `./manifests/`.

Serving (GPU, bf16, both pins): resident set 20.5 GB RSS, 15.3 GB VRAM (the process); start to ready about 35 s (sha256 verification of both indexes included). p50 query latency 490 ms on b3, 387 ms on b2 (30 requests each; exact CPU FAISS flat search over 8 GB dominates, model forward is a few ms).
Unit: `tauceti-finder.service` enabled, restart verified (sockets reappear 0666 in 0755 dirs).

Commands:
```
cd ~/lean/workboots/local-finder
.venv/bin/python build_finder.py b3-11bc13335fb9 --device cuda:0     # then b2-b5500936b563, same flags (reads decls.jsonl type_full)
# logs/build-both.sh wraps both under /usr/bin/time -v, logs in ~/.local/share/tauceti-finder/logs/
sed -i 's/^TAUCETI_FINDER_PINS=.*/TAUCETI_FINDER_PINS=b3-11bc13335fb9 b2-b5500936b563/' ~/.config/tauceti-finder/finder.env
./install-service.sh --enable
./client-smoke.sh --socket "$XDG_RUNTIME_DIR/tauceti-finder/<pin>/finder.sock"
```
