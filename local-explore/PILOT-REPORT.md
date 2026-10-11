# LeanExplore describer pilot report (2026-10-08)

Pin `b3-11bc13335fb9`, 200 pilot declarations (137 bespoke, 63 mathlib vocabulary), three describers, `--max-tokens 1000`.
Per-call logs: `pilot/explore/<model>/describe-log.jsonl`. No key was written to any file.

## Actual spend

Computed from the logged token counts at the `models.json` prices (equal to the tool's own running estimate; the OpenRouter dashboard was not consulted).

| arm | model | cost | cap |
|---|---|---|---|
| A | google/gemini-3-flash-preview | $0.1389 | 0.60 |
| B | anthropic/claude-haiku-5.5 | $0.0602 | 0.25 |
| C | qwen/qwen3.8-flash | $0.1535 (run 1 $0.0659 + resumed run 2 $0.0876) | 0.25 per run |
| total | | **$0.3526** | worst case in plan $1.34 |

Dry-run estimates (150 output tokens/decl assumed): A $0.1714, B $0.0313, C $0.0385, all within 2x of the plan, so the run proceeded. Actual B cost was 1.9x its estimate and C 4x (see tokens below).

Qwen3.8-flash was run twice: the first run lost 164 of 200 calls (HTTP 429 upstream rate limit from the Alibaba provider after 3 retries, plus calls that spent the 1000-token budget on reasoning and hit `finish_reason=length`). I re-ran the same command once (same caps, still within the arm cap; the tool skips already described rows). The second run described 33 more and failed 131. Final: 69 of 200 described (the log's first-run file is `describe-log.run1.jsonl`). I did not retry further.

## Describe stage statistics

| | gemini-3-flash-preview | claude-haiku-5.5 | qwen3.8-flash |
|---|---|---|---|
| calls (logged API responses) | 200 | 200 | 267 (+97 failed error lines) |
| failures / retries | 0 / 0 | 0 / 0 | 131 declarations never described; 93 retried calls; 208 of 267 responses `finish_reason=length` |
| mean latency | 1.6 s | 2.3 s | 18.6 s |
| p95 latency | 2.3 s | 4.3 s | 24.3 s |
| input tokens (billed) | 166,735 (834/decl) | 262,095 (1,310/decl) | 237,330 over 267 calls |
| output tokens | 18,513 (93/decl) | 68,038 (340/decl) | 250,836 over 267 calls (reasoning) |
| cost | $0.1389 | $0.0602 | $0.1535 |
| cost per declaration (200) | $0.000695 | $0.000301 | $0.000768 per described row ($0.00221 per row actually described, 69) |

Notes: Haiku writes much longer descriptions than Gemini (340 vs 93 tokens) and its tokenizer counts about 1.57x the Qwen-proxy input estimate; one Haiku call hit the length cap (row truncated mid-sentence, scored vague). Gemini's billed output count may exclude hidden reasoning, but the cost equals the logged tokens at list price.

## Rank metric (30 hand-written queries, k=50, rerank 25, CPU)

Queries were written from name/kind/signature/docstring only, before any description was seen (`pilot/rank-queries.csv`, blank copy `pilot/rank-queries.blank.csv`). Index is each arm's 200-row DB (not the full corpus), so ranks are among 200 rows; the target is easy to find in absolute terms. Rank 51 = not in top 50. `rank_no_description` is a fourth index (`pilot/explore/none_fallback`) embedded from name+docstring+signature (`--embed-fallback-text`). Qwen's index has only the 69 described rows in the vector index (undescribed rows reachable only by BM25 on names), which is its real state after the failed run.

| arm | cells | median rank | top-10 hit | top-1 | not found |
|---|---|---|---|---|---|
| A gemini | all 30 | 1 | 30/30 | 29 | 0 |
| | bespoke (16) | 1 | 16/16 | 15 | 0 |
| | mathlib (14) | 1 | 14/14 | 14 | 0 |
| B haiku | all | 1 | 30/30 | 29 | 0 |
| | bespoke | 1 | 16/16 | 15 | 0 |
| | mathlib | 1 | 14/14 | 14 | 0 |
| C qwen | all | 2 | 21/30 | 13 | 5 |
| | bespoke | 4.5 | 10/16 | 5 | 4 |
| | mathlib | 1 | 11/14 | 8 | 1 |
| no description (fallback text) | all | 4 | 23/30 | 9 | 0 |
| | bespoke | 4.5 | 12/16 | 5 | 0 |
| | mathlib | 4 | 11/14 | 4 | 0 |

A and B differ by 0 queries of 30. Caveat: the queries paraphrase docstrings and the corpus is 200 rows, so this is a smoke-level signal that good descriptions beat the fallback text (30 vs 23 top-10), not a full-index quality estimate.

## Blind scoring

`pilot/score-shuffled.csv` (600 rows, model column hidden, shuffled with seed 20261008) was scored by me against signature + docstring, before unblinding through `pilot/score-key.csv` (row_id -> model, name, cell_vocab, cell_kind). Verdicts are copied to `pilot/score-<model>.csv` (`verdict` column). Rubric as in PILOT-PLAN. Qwen rows with no description are `missing` (counted as not right). Limits: one reviewer (the same model family as one arm, though blind), signatures truncated to about 240 chars in my display, docstrings to 160, so my reading was lenient on details I could not see; LaTeX in descriptions was not treated as Lean syntax. Treat `right` rates as upper-leaning estimates, differences of a few rows as noise.

| model | cell | n | right | vague | wrong | missing |
|---|---|---|---|---|---|---|
| gemini-3-flash-preview | all | 200 | 187 (93.5%) | 12 (6.0%) | 1 (0.5%) | 0 |
| | bespoke | 137 | 127 (92.7%) | 9 (6.6%) | 1 (0.7%) | 0 |
| | mathlib | 63 | 60 (95.2%) | 3 (4.8%) | 0 | 0 |
| claude-haiku-5.5 | all | 200 | 189 (94.5%) | 9 (4.5%) | 2 (1.0%) | 0 |
| | bespoke | 137 | 129 (94.2%) | 6 (4.4%) | 2 (1.5%) | 0 |
| | mathlib | 63 | 60 (95.2%) | 3 (4.8%) | 0 | 0 |
| qwen3.8-flash | all | 200 | 67 (33.5%) | 2 (1.0%) | 0 | 131 (65.5%) |
| | bespoke | 137 | 36 (26.3%) | 2 (1.5%) | 0 | 99 (72.3%) |
| | mathlib | 63 | 31 (49.2%) | 0 | 0 | 32 (50.8%) |

Among the 69 qwen rows that exist, 67 were right (97%): its quality when it answers is fine, its failure is the delivery (rate limits, reasoning burning the 1000-token cap).

Examples (full text in the score CSVs):

* gemini right: `TauCeti.IsHyperellipticFunctionField` (genus >= 2 plus transcendental x with F/k(x) separable of degree 2, exactly the docstring); `TauCeti.GeneralLinear.isAffine_groupScheme`.
* gemini wrong: `TauCeti.AffineChain.subdivision_single` says the sum runs over permutations of S_k; the signature indexes `Fin (k+1)` so it is S_{k+1}.
* gemini vague: `TauCeti.ContCohomology.inverseConjugationHomotopy2` (only restates types, never says what the homotopy does); `TauCeti.connectedPointedCoverSetoid` (invents "degree n").
* haiku right: `TauCeti.ExactStructure.suspensionToStableOfPresentationsIso_inv_app` (correctly says the inverse is the comparison in the opposite direction); `TauCeti.tendsto_truncatedPerronKernel`.
* haiku wrong: `TauCeti.GeneralLinear.weightTorusCoordinateMap_determinantGroupLike` claims "exactly when the weights sum to zero" (statement is only the implication); `TauCeti.dualNumberProjectiveResolution_π_f_zero` misidentifies the degree-zero term as `k[ε]/(ε)` instead of `k[ε]`.
* haiku vague: `TauCeti.GridRectangleDecomposition.IsRecutOfRightEqLeft` (mixes up which decompositions the sides belong to); `TauCeti.Symplectic.coordinateMap` (misreads `O(GL)` notation as an orthogonal quotient); the one truncated row (`TauCeti.isOpen_image_schwarzChristoffelPrimitive`).
* qwen right: `Subrepresentation.toRepresentation_apply`; qwen missing: 131 rows, mostly bespoke.

## Decision (plan rule)

Rule: cheapest model with >= 90% right and <= 2% wrong on bespoke cells, or best rank metrics if they differ by more than 2 queries of 30.

* gemini: 92.7% right, 0.7% wrong, passes, $0.000695/decl.
* haiku: 94.2% right, 1.5% wrong, passes, $0.000301/decl (2.3x cheaper).
* qwen: fails (26% right because 72% of rows were never produced; with the reasoning default it cannot finish inside `--max-tokens 1000`).
* Rank metrics tie for A and B (0 of 30 apart).

**Chosen: `anthropic/claude-haiku-5.5`.** Its 1.5% wrong rate is 2 rows of 137; that is within noise of the 2% limit, so a larger blind sample (or a second reviewer) before the full build is worth the few cents.

## Full b3 build extrapolation (haiku)

Counts: README cost model (non-strict reuse) leaves **124,008** rows to describe. `build_explore.py --dry-run --reuse-strict` (full b3 adapt written to the scratchpad, not to any shared dir) leaves **126,884** rows (374,121 reused, 2,876 skipped for docstring mismatch).

The README's $20 for haiku assumed 110 output tokens and the Qwen-tokenizer input count. Measured haiku: 1,310 billed input and 340 output tokens per pilot description (with an empty dependency block). In the real build the dependency block is filled (the dry-run estimate rises from 813 to about 1,090 proxy tokens per declaration, x1.34).

| scenario | rows | per-decl cost | total |
|---|---|---|---|
| README cost model | 124,008 | 0.00016 | $20 |
| pilot-measured as is (no dependency growth) | 124,008 | $0.000301 | **$37.3** |
| pilot-measured, input scaled x1.34 for dependency context | 124,008 | $0.000346 | **$42.9** |
| same, strict reuse | 126,884 | $0.000346 | **$43.9** (pilot-as-is: $38.2) |

Plan on about $43 to $44 for haiku (the README figure is low by about 2x because of the 3x longer outputs); add the usual 1.5x margin for `--max-spend` (about $65). For comparison gemini measured: $0.000695/decl as is, about $0.00084 with dependency growth, i.e. $86 to $104 for 124,008 rows. A cheaper variant is a shorter-output prompt for haiku (its output is 73% of its bill).

## Commands run (all from `~/lean/workboots/local-explore`, `HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES=`, `PIN=b3-11bc13335fb9`, `E=$PWD/pilot/explore`)

```bash
# per arm t in google_gemini-3-flash-preview anthropic_claude-haiku-5.5 qwen_qwen3.8-flash
.venv/bin/python build_explore.py $PIN --stage adapt --names pilot/pilot-names.txt --out-dir $E/$t --force
.venv/bin/python build_explore.py $PIN --stage describe --dry-run --names pilot/pilot-names.txt --out-dir $E/$t --informalize-model $m --est-output-tokens 150
# spend (key loaded with export OPENROUTER_API_KEY="$(cat ~/.config/tauceti/openrouter_key.txt)" in the same shell command)
.venv/bin/python build_explore.py $PIN --stage describe --names pilot/pilot-names.txt --out-dir $E/$t --informalize-model $m --max-tokens 1000 --max-spend {0.60|0.25|0.25} --i-approve-spend
# qwen once more, identical flags (resume)
# fallback-text baseline (no model): adapt to $E/none_fallback, then embed with --embed-fallback-text
.venv/bin/python build_explore.py $PIN --stage embed --out-dir $E/$t --device cpu [--embed-fallback-text]
.venv/bin/python build_explore.py $PIN --stage index --out-dir $E/$t --device cpu
.venv/bin/python server.py --explore-root $PWD/pilot/explore --port 18090 --device cpu --timeout 1200   # then stopped
# queries: GET 127.0.0.1:18090/search?q=...&k=50&pin=<dir>   (script in session scratchpad; results in pilot/rank-queries.csv)
.venv/bin/python build_explore.py $PIN --stage export --out-dir $E/$t --names pilot/pilot-names.txt --export-to pilot/score-$t.csv
# full-build count only (no spend): adapt to a scratch dir, then
.venv/bin/python build_explore.py $PIN --stage describe --dry-run --reuse-from ~/.local/share/tauceti-explore/upstream/20260714_172516 --reuse-strict --out-dir <scratch> --informalize-model anthropic/claude-haiku-5.5
```

## What went wrong / caveats

* Qwen3.8-flash: 429 rate limits (upstream shared pool) and reasoning tokens exhausting the 1000-token cap; only 69/200 described after two runs ($0.15, the most expensive per usable row). No further retries were made. If qwen is wanted, it needs reasoning disabled or a higher per-call cap, both outside this pilot's approval.
* Dependency context was nearly empty in the pilot (2/200 rows had one), so real prompts will be longer.
* Rank test uses 200-row indexes and queries that echo docstrings; the retrieval gain over the fallback text is directional.
* Scoring is by one reviewer and lenient on detail hidden by truncated signatures; zero `wrong` rows would have been a red flag, the 3 found are real errors but the true rate may be somewhat higher.
* The embed step took 5 to 10 min per arm on CPU under host load (0.3 to 0.6 texts/s); the first server startup was done once, no GPU used, server stopped afterwards (pid file `pilot/server.pid`).
* The detached smoke build (pid 3409823) was left untouched.
