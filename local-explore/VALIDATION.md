# Validation of server.py against the slice index (2026-10-08)

**Scope and honesty.** The real `decls.jsonl` landed only after the slice had been built, and the full
index (about 500k rows, needs descriptions) does not exist yet. The service was validated against a
**220-row stand-in slice** built by `dev/make_sample_decls.py` (130 random Mathlib rows taken from the
prebuilt LeanExplore data, with their upstream descriptions and embeddings reused by name; 90 TauCeti
rows scraped by regex, no descriptions, embedded from `name. docstring signature` via
`--embed-fallback-text`). The slice is random, so most queries have **no relevant declaration in it**:
the top-3 lists below demonstrate that the pipeline runs and is deterministic, not retrieval quality.
Quality has to be judged on the real index (see PILOT-PLAN.md, rank metric).

Setup: `server.py --explore-root <slice> --socket-dir /tmp/xe --port 18089 --device cpu --threads 8
--rerank-top 25 --timeout 1200`; Python 3.14.4, lean-explore 1.3.0, Qwen3-Embedding-0.6B
`97b0c614be4d`, Qwen3-Reranker-0.6B `e61197ed4502` (both pinned snapshots, `HF_HUB_OFFLINE=1`).

## Protocol checks

| check | result |
|---|---|
| `GET /health` on `/tmp/xe/b3-11bc13335fb9/explore.sock` | `{"ok":true,"tool":"explore","manifest":{"default_pin":"b3-11bc13335fb9","pins":{...},"models":...}}`, 200 |
| `GET /search?q=&k=3` | `{"tool":"explore","pin":"b3-11bc13335fb9","hits":[{name,kind,module,signature,description,score}],"ms":...}` |
| empty `q` | 400 `{"error":"missing or empty q"}` |
| unknown `pin` | 404 `{"error":"unknown pin 'nope'; loaded: [...]"}` |
| TCP `--port` | same responses on 127.0.0.1:18089 |
| per-pin socket (contract amendment) | one socket per loaded pin at `<socket-dir>/<pin>/explore.sock`; a request without `pin` uses that socket's pin; `pin=` overrides |
| determinism | every query run twice: identical names and scores (5/5) |
| `rerank_top` | `--rerank-top` (default 25), per-request override `&rerank_top=`, 0 disables |

## Queries (k=3, rerank_top 25, CPU)

Latency caveat: the host was at **load average 220-290** (S0's Lean builds on 128 threads) for the whole
session, so wall-clock numbers are inflated by an unknown factor, possibly 10x or more. Two runs per query.

| # | query | top-3 (score) | wall s (run 1 / run 2) |
|---|---|---|---|
| 1 | continuous image of a compact set is compact | `IsCompact.smul` 1.400, `IsCompact.sSup_lt_iff_of_continuous` 1.187, `IsClosed.mul_right_of_isCompact` 0.986 | 41.4 / 14.9 |
| 2 | List.map | `«term_→ᴬ[_]_»` 1.240, `RestrictedProduct.map` 1.237, `ContinuousAlternatingMap.continuous_toContinuousMultilinearMap` 1.205 | 18.5 / 19.9 |
| 3 | sum of a finite geometric series | `ENNReal.tsum_coe_ne_top_iff_summable_coe` 1.306, `Summable.tsum_ofReal_ne_top` 1.178, `Summable.tsum_const_smul` 1.167 | 20.8 / 20.0 |
| 4 | a group homomorphism is injective iff its kernel is trivial | `WithVal.algebraMap_left_injective` 1.320, `TopCat.mono_iff_injective` 1.286, `is` 1.115 | 12.5 / 13.5 |
| 5 | Nat.Prime divides product | `RestrictedProduct.map` 1.259, `hasProdUniformly_iff_tendstoUniformly` 1.087, `RestrictedProduct.evalRingHom_apply` 1.066 | 18.1 / 107.0 |

Queries 1 and 3 do pick topical rows (compactness, summability) from a slice that has few; 2, 4, 5 have
no target in the slice. Expected steady-state latency on an idle host: not measured. The cost is dominated
by 25 reranker forward passes (0.6B parameters, 256 tokens) plus one query embedding, so on 8 idle cores
expect low single-digit seconds; re-measure after the host quiets down and after the GPU driver is fixed
(then drop `CUDA_VISIBLE_DEVICES=` and `--device cpu`).

## Bugs found and fixed while validating

* `bm25s` raises when `k` exceeds the corpus (any index with fewer than 1000 rows): `search_scored`
  now clamps `bm25_k` to the corpus size.
* The pinned reranker snapshot in the HF cache lacked `.gitattributes`, `README.md` and `chat_template.jinja`,
  so `HF_HUB_OFFLINE=1` startup failed with `IncompleteSnapshotError`. Fixed by one online
  `snapshot_download(revision=...)` of both repos (documented in README, "Models").
* Warm-up search timed out at the default 60 s under host load; use `--timeout` (the unit sets a larger one).

# Full-index validation (2026-10-09, GPU service)

Service: `tauceti-explore.service` (systemd --user), both pins, `--device cuda`, rerank_top 25, k=50, sockets under
`$XDG_RUNTIME_DIR/tauceti-explore/<pin>/explore.sock`. Indexes: b3 510,862 / 510,999 rows embedded (137 undescribed rows are BM25-name-only),
b2 473,322 / 473,395 (73 undescribed). Queries: the pilot's 30 hand-written rank queries (`pilot/rank-queries.csv`, written blind from name/signature/docstring),
script `dev/rank_queries.py <pin>`. Host busy with a CPU-heavy bench smoke build, so latencies are an upper bound.

| pin | queries | median rank | top-10 | top-1 | not in top 50 |
|---|---|---|---|---|---|
| b3 (full, 510k rows) | 30 | 1 | 29/30 | 18 | 0 |
| b2 (full, 473k rows) | 11 (the targets that exist in b2; 19 are b3-only) | 2 | 11/11 | 4 | 0 |
| pilot haiku (200-row corpus, for comparison) | 30 | 1 | 30/30 | 29 | 0 |

Misses (b3): `TauCeti.AlgebraicGeometry.SchemeWeilDivisor.isLimitKernelForkSheafHomOfLE` rank 21 (a plumbing lemma with many near-duplicate siblings);
`Subrepresentation.toRepresentation_apply` 7; `SheafOfModules.isFiniteType_biprod` 5; `AmbientIsotopy.totalHomeomorph_symm_fst` 4.
Top-1 drops from 29/30 to 18/30 against 500k competitors instead of 200, as expected; median rank and top-10 hold (one query falls out of the top 10 versus the pilot).
b2 ranks agree with b3 on the shared targets. p50 /search latency over these 41 queries: about 145 ms (b3), 149 ms (b2), GPU rerank.
