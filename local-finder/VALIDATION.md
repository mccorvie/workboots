# VALIDATION: CPU-only run of builder and server (2026-10-08)

The GPU is down (driver mismatch), so everything here ran on CPU (fp32). No GPU job was started.

## Setup

- Input: 200 rows of the upstream Mathlib corpus (`mathlib4_28_0.jsonl`, every 1123rd row) mapped into our `decls.jsonl` schema by
  `scripts/upstream_to_decls.py` (`signature` := upstream `typeFull`, `docstring` := `""` where upstream is null). The adapter round trip
  `format_entry(adapt_decl(decl)) == format_entry(upstream_row)` holds for all 200 rows (0 mismatches). The real extraction did not exist yet.
- Host: 128 hardware threads, 247 GB RAM, **load average 120-160 during the whole run** (other agents were compiling Lean), so every timing below is a
  pessimistic floor.
- Pins: lean-finder `69b764614453057013e411a8c8081f447cda64e7`, model `delta-lab-ai/lean-finder` @ `a9e312299c4d68f020662a68d70c4e3979781f91`,
  torch 2.9.0+cu128, transformers 4.57.5, faiss-cpu 1.13.2, numpy 2.5.3, Python 3.12.9. Embedding dim 4096.
- Output went to a scratch data root (`TAUCETI_FINDER_DATA`, `TAUCETI_INDEX_DATA`), not to `~/.local/share/tauceti-finder/<pin>/`.

## Build

```
build_finder.py val-200 --device cpu --limit 200 --manifest-copy-dir ''
```

| item | value |
|---|---|
| rows / kinds | 200 (theorem 153, definition 46, inductive 1) |
| passage tokens | 23,670 (mean 118) |
| model load (fp32, CPU, page cache warm) | 5.5 s |
| embed time | 751 s = 12.5 min, **31.5 tokens/s, 0.27 rows/s** (7 length-sorted batches, <= 32 rows / 32,768 padded tokens) |
| peak RSS of the builder | ~30 GB (fp32 weights 32 GB mapped, matches the 32 GB estimate) |
| `corpus.index` | 3,276,845 bytes = 200 x 4096 x 4 + 45 header (`faiss.IndexFlatIP`) |
| `corpus.jsonl` | 176,245 bytes |
| manifest fields | pin, model repo + revision, lean_finder_commit, embedding_dim 4096, rows, sha256 + bytes of corpus.jsonl and corpus.index, source decls sha256, device, dtype, timing, versions, `type_full_source` |

The builder needed no patch to run on CPU (the vendored upstream code hard-codes `"cuda"` in `server.py` and `build_index.py`; our
`finder_common.Embedder` takes the device and dtype as parameters and otherwise repeats the upstream sequence line for line).
CPU uses fp32 (default; bf16 on CPU was not measured); the GPU path keeps upstream's bf16. Vectors from the two are cached in separate buckets.

Fidelity of the encoder (run earlier by the predecessor with `scripts/check_against_upstream.py --stride 1123 --limit 200 --device cpu --dtype float32`, output
`scratchpad/val/check-fp32.json`, not re-run by me): our fp32 CPU vectors vs the vectors stored in upstream's shipped 224,722-row index, cosine mean 0.99991, min 0.99987;
every one of the 200 rows retrieves itself at rank 1 out of the full upstream index. That validates tokenizer, entry format, left-padding last-token pooling and
normalisation end to end. (The same run had 27.5 tokens/s under similar load.)

## Server

```
server.py --pin val-200 --data-dir <scratch>/fdata --device cpu --unix-socket sock/finder.sock --port 18090
```

- Model ready after ~26 s (warm cache), index loaded in 0.0 s, "ready" ~1.5 min after launch (including the warm-up forward pass under load).
- Socket directory mode 755, socket mode 666. `GET /health` -> `{"ok":true,"tool":"finder","manifest":{...}}`; the manifest carries model revision,
  lean-finder commit, device/dtype, dim, default pin, per-pin manifests and package versions.
- Error handling: empty `q` -> 400 `{"error":"missing or empty query parameter q"}`; `k=0` -> 400; unknown `pin` -> 404; unknown path -> 404 (all JSON).
- Determinism: two identical queries gave byte-identical responses (same md5).
- Hit shape: `{name, kind, module, signature, description, score}` with `description` empty (as in the contract).
- Bug found and fixed while testing: a unix-socket path longer than 107 bytes made uvicorn's thread die with `AF_UNIX path too long`, but the server still logged
  "ready" and kept running with no listener. It now logs an error and exits 1 if any listener failed to start. (The scratch directory is long, so the test used a relative
  socket path.)

## Queries

Only 200 rows are indexed, so a query can retrieve only what is in those rows; I wrote four queries paraphrasing declarations in the slice, plus the suggested one
(whose answer, `IsCompact.image`, is not in the slice, so its hits are noise).

Latency is wall clock from `curl` over the unix socket, including query embedding (about 40-60 tokens through the 8B model) and the exact FAISS search; search over 200 rows is negligible.

| query | top 3 (score) | latency |
|---|---|---|
| continuous image of a compact set is compact | `AlgebraicGeometry.quasiCompact_of_compactSpace` (0.460), `AlgebraicGeometry.quasiCompact_isStableUnderBaseChange` (0.302), `Continuous.matrix_submatrix` (0.292) | 18.9 s (first query, cold) |
| real power is strictly monotone in the base for positive exponent | `Real.rpow_lt_rpow_iff` (0.797), `continuous_zpow` (0.375), `StrictAnti.mapsTo_Ioi` (0.354) | 6.4 s |
| norm of equivalent p-adic sequences agree | `PadicSeq.norm_equiv` (0.957), `NumberField.mixedEmbedding.norm_nonneg` (0.326), `IsCauSeq.bounded` (0.301) | 4.6 s |
| frequently holds if the predicate holds for all | `Filter.Frequently.of_forall` (0.880), `Filter.exists_eventuallyEq_const_of_forall_separating` (0.380), `forall_restrictGermPredicate_of_forall` (0.299) | 4.4 s |
| slope of a convex function is bounded by the left derivative | `ConvexOn.slope_le_leftDeriv` (0.933), `Finset.sum_schlomilch_le'` (0.237), `Real.HolderConjugate.sub_one_ne_zero` (0.175) | 2.1 s |
| Holder conjugate exponents p minus one nonzero | `Real.HolderConjugate.sub_one_ne_zero` (0.924), `Fin.zero_ne_one'` (0.312), `not_integrableOn_Ioi_rpow` (0.298) | 3.3 s |

All four in-slice queries put the intended declaration first with a wide score margin (0.80-0.96 vs <= 0.38). The earlier predecessor run against the full
upstream index (`check-fp32.json`) returned `IsCompact.image_of_continuousOn`, `IsCompact.image`, ... for "continuous image of a compact set is compact" (scores 0.96, 0.96), which
also shows the query path (instruction prefix + pooling) behaves as intended on the real corpus.

Expect single-digit-ms to ~0.1 s per query on the GPU (one short forward pass, plus ~6 GB exact inner-product search: ~10-50 ms on the host CPU for 350k x 4096).

## Not validated here

- Any real extraction (`decls.jsonl` for b2/b3): not present yet. `build_finder.py` and the `typeFull` path were exercised with 5,000-row dry runs on a merged core-Lean extraction
  (see FORMAT-CHECK.md) but not embedded.
- GPU: bf16 numerics, throughput, and `--device cuda:0` run-time paths. The dry run was exercised with `--device cuda:0` for token statistics only.
- Concurrency: the server serialises embeddings with a lock; not load tested.
- systemd unit: installed nowhere, not enabled, not started.

## GPU build (2026-10-08/09)

Indexes built on the RTX PRO 6000 Blackwell (bf16, `type_full` from `decls.jsonl`, 51 null `type_full` rows in b3 rebuilt from `signature`)
and served on the GPU from per-pin sockets. 10 natural-language paraphrase queries per pin, `k=50`, rank of the intended declaration
(`-` = not in top 50). Same 10 queries on both pins:

| # | query | intended | b3 | b2 |
|---|---|---|---|---|
| 1 | the image of a compact set under a continuous map is compact | IsCompact.image | 1 | 1 |
| 2 | a prime number's only divisors are one and itself | Nat.Prime.eq_one_or_self_of_dvd | 2 (top: Nat.Prime.divisors) | 2 |
| 3 | swapping the order of summation in a double sum over finite sets | Finset.sum_comm | 1 | 1 |
| 4 | square root of a real number is never negative | Real.sqrt_nonneg | 1 | 1 |
| 5 | the number of elements in a subgroup divides the size of the group | Subgroup.card_subgroup_dvd_card | 1 | 1 |
| 6 | a square of any element in an ordered ring is nonnegative | mul_self_nonneg | 6 (top: Lean.Grind.OrderedRing.sq_nonneg) | 6 |
| 7 | composition of two continuous functions is continuous | Continuous.comp | 1 | 1 |
| 8 | inclusion exclusion for two finite sets: card of union plus card of intersection | Finset.card_union_add_card_inter | 4 (top: Set.ncard_inter_add_ncard_union) | 4 |
| 9 | degree of a product of polynomials is the sum of the degrees | Polynomial.degree_mul | 1 | 1 |
| 10 | the exponential of a sum equals the product of exponentials for real numbers | Real.exp_add | 1 | 1 |

Ranks: b3 `1 2 1 1 1 6 1 4 1 1`, b2 identical. No misses in the top 50; 7/10 at rank 1, all within the top 6 (the non-1 hits are near-synonyms
of the intended lemma). Self-retrieval on the served b3 index (40 random rows, query = name + first 200 chars of the embedded type):
36/40 at rank 1. Query latency over 30 requests per pin (curl through the unix socket, host under load from a bench build and a describe run):
p50 490 ms (b3), 387 ms (b2), max 566 ms; the FAISS flat search runs on CPU.
