# Describer pilot plan (workstream S3)

Status: **ready to run, not run** (no OpenRouter key on this host; nothing here has spent money).
Needs the operator's approval for roughly $0.25 expected, $1.35 worst case (arithmetic below).

## What is being decided

Which OpenRouter model writes the LeanExplore descriptions for the TauCeti declarations
(and for the Mathlib/core rows that the prebuilt data cannot seed). Decision criteria:
description quality on TauCeti vocabulary (bespoke definitions the model has never seen),
cost per declaration, and retrieval effect (rank of the target declaration for a natural query).

## Sample

`select_pilot.py` over `~/.local/share/tauceti-index/b3-11bc13335fb9/decls.jsonl`
(already run; output committed under `pilot/`):

* Population: 87,221 TauCeti rows (module `TauCeti.*`; the plan's "38k" was a grep undercount),
  84,592 eligible after dropping constructors (`ctor`), auto-generated names and rows without a signature.
* 200 rows stratified by kind group (theorem, def, structure/class/inductive, instance) and by
  vocabulary: *bespoke* = the signature or docstring mentions a name that only TauCeti defines;
  *mathlib* = Mathlib/core vocabulary only. Proportional allocation with a floor of 8 per cell
  (`pilot/pilot-cells.md`): bespoke 137 (theorem 97, def 23, instance 9, structure 8),
  mathlib 63 (theorem 32, def 14, instance 9, structure 8). Seed 20261008, deterministic.
* `pilot/pilot-names.txt` (names), `pilot/pilot-200.jsonl` (records), `pilot/rank-queries.csv`
  (30 declarations round-robin over cells; the operator or a reviewer fills the `query` column by hand).

## Candidate models (prices from `https://openrouter.ai/api/v1/models`, public endpoint, 2026-10-08, USD per 1M tokens; in `models.json`)

| arm | model id | input | output | why |
|---|---|---|---|---|
| A | `google/gemini-3-flash-preview` | 0.50 | 3.00 | LeanExplore's own default; the quality reference |
| B | `anthropic/claude-haiku-5.5` | 0.10 | 0.50 | cheapest of the strong Western small models; strong at formal-math paraphrase |
| C | `qwen/qwen3.8-flash` | 0.15 | 0.47 | cheap open-weight family; shows how low the price can go |

Alternatives priced in `models.json` if the operator prefers: `deepseek/deepseek-v4.1-flash` (0.30/1.20),
`google/gemini-3.1-flash-lite` (0.25/1.50), `openai/gpt-5.6-luna` (0.20/1.20), `qwen/qwen3.7-flash` (0.03/0.13).

## Spend arithmetic (200 declarations per model)

Measured by `build_explore.py --dry-run` on the real sample with LeanExplore's own prompt
(`prompt.txt`; token count from the Qwen3 tokenizer, a proxy): **796 input tokens per declaration**,
159,319 for 200. In the pilot the dependency block is empty (no dependency has a description yet), so
real builds are somewhat larger; add 30% margin: 207k.
Output: LeanExplore's mean description is 283 chars, about 80-110 tokens; assume 150 per declaration
= 30,000, and as a worst case 1,500 per declaration (a reasoning model that burns thinking tokens, billed
as output) = 300,000. `--max-tokens 1000` caps any single call.

| model | input $ (207k tok) | expected output $ (30k tok) | expected total | worst-case output $ (300k tok) | worst total |
|---|---|---|---|---|---|
| A gemini-3-flash-preview | 0.207 x 0.50 = 0.104 | 0.030 x 3.00 = 0.090 | **$0.19** | 0.300 x 3.00 = 0.900 | $1.00 |
| B claude-haiku-5.5 | 0.207 x 0.10 = 0.021 | 0.030 x 0.50 = 0.015 | **$0.04** | 0.300 x 0.50 = 0.150 | $0.17 |
| C qwen3.8-flash | 0.207 x 0.15 = 0.031 | 0.030 x 0.47 = 0.014 | **$0.05** | 0.300 x 0.47 = 0.141 | $0.17 |
| total | | | **$0.28** | | **$1.34** |

Dry-run estimates without margin: A $0.170, B $0.031, C $0.038 (sum $0.24). Both numbers are far
under the $2 budget; `--max-spend 0.60` on arm A and `--max-spend 0.25` on B and C make the tool
refuse before spending and abort mid-run if the running estimate exceeds the ceiling.

## Commands

```bash
cd ~/lean/workboots/local-explore
export HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES=
PIN=b3-11bc13335fb9
E=$PWD/pilot/explore
python3 -I select_pilot.py ~/.local/share/tauceti-index/$PIN/decls.jsonl --out-dir pilot   # already done

for m in google/gemini-3-flash-preview anthropic/claude-haiku-5.5 qwen/qwen3.8-flash; do
  t=${m//\//_}
  .venv/bin/python build_explore.py $PIN --stage adapt --names pilot/pilot-names.txt --out-dir $E/$t --force
  # 1. estimate, no network:
  .venv/bin/python build_explore.py $PIN --stage describe --dry-run --names pilot/pilot-names.txt \
      --out-dir $E/$t --informalize-model $m --est-output-tokens 150
done

# 2. spend (the operator provides the key and the flag; the tool prints the estimate first):
export OPENROUTER_API_KEY=...        # not on this host
for m in google/gemini-3-flash-preview anthropic/claude-haiku-5.5 qwen/qwen3.8-flash; do
  t=${m//\//_}
  .venv/bin/python build_explore.py $PIN --stage describe --names pilot/pilot-names.txt --out-dir $E/$t \
      --informalize-model $m --max-tokens 1000 --max-spend 0.60 --i-approve-spend
  # per-call usage, latency and cost land in $E/$t/describe-log.jsonl
done

# 3. retrieval effect (CPU, free): embed + index each pilot DB, then serve the three as pins
for m in ...; do t=${m//\//_}
  .venv/bin/python build_explore.py $PIN --stage embed --out-dir $E/$t --device cpu
  .venv/bin/python build_explore.py $PIN --stage index --out-dir $E/$t --device cpu
done
.venv/bin/python server.py --explore-root $E --port 18090 --device cpu     # pins = the three dirs
# rank of the target name for each query in pilot/rank-queries.csv:
#   curl -G localhost:18090/search --data-urlencode "q=..." -d k=50 -d pin=google_gemini-3-flash-preview

# 4. scoring sheets (one CSV per model, blank verdict column):
for m in ...; do t=${m//\//_}
  .venv/bin/python build_explore.py $PIN --stage export --out-dir $E/$t --names pilot/pilot-names.txt --export-to pilot/score-$t.csv
done
```

(Step 3 needs a `MANIFEST.json` per pilot directory; the index stage writes one. The embed stage is
free: a local Qwen3-Embedding-0.6B on CPU, about 200 short texts per model.)

## Scoring sheet

`pilot/score-<model>.csv`, one row per declaration: name, kind, module, signature, docstring,
description, model, **verdict** (`right` / `vague` / `wrong`), notes. Rubric, applied blind to the model
column (shuffle rows across the three files before reviewing):

* **right**: states the same mathematical content as the signature, names the objects in words,
  no Lean syntax, no invented hypotheses.
* **vague**: correct but generic ("a lemma about maps"), or omits a key hypothesis or conclusion.
* **wrong**: states something false or different, or merely restates the name.

Report per model, split bespoke vs mathlib vocabulary: %right, %wrong; mean output tokens; actual
cost per declaration from `describe-log.jsonl`; and from `rank-queries.csv` the median and
top-10 hit rate of the target (including the `rank_no_description` column, the fallback-text-only
baseline from `--embed-fallback-text`).

## Decision rule

Pick the cheapest model with >= 90% right (and <= 2% wrong) on the bespoke cells, or the one with the
best rank metrics if they differ by more than 2 queries of 30. Then extrapolate (README cost model).
