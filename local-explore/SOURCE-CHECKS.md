# LeanExplore source checks (workstream S3, 2026-10-08)

*Re-verified by the successor agent: the verdicts below were re-read against the sdist and the downloaded
upstream database (schema, version, row counts, `prompt.txt` omission all confirmed; the Python 3.14 row was
exercised by the whole build and server). Updates since the first pass: S0's real `decls.jsonl` exists
(510,999 rows b3, 473,395 b2, field `type_full` = ppExpr type at width 120, `kind` is doc-gen4's incl. `ctor`);
the adapter's statement text is `<kind> <name> : <type_full>` when `type_full` is present, else the signature;
constructors are dropped by default (`--skip-kinds ctor`).*

Checked against **PyPI `lean-explore` 1.3.0** (released 2026-08-02; identical to git
`28eac03`/`6b25f86`) and **git HEAD `17b9d6c`** (2026-09-28, "Refactor modules and improve
test coverage", one commit after the release; unreleased). 1.3.0 is what we pin; HEAD is cited
where it differs. Source trees were read from the sdist (`lean_explore-1.3.0.tar.gz`) and a clone
of https://github.com/lean-explore/lean-explore. File paths below are 1.3.0 paths unless
marked HEAD.

| # | Question | Verdict |
|---|---|---|
| a | Input format; schema; bypass parse? | Reads **both** doc-gen4 BMP and `api-docs.db` (SQLite, doc-gen4 >= v4.29.0-rc2). Parse can be bypassed: the schema is one table, `declarations`, created from `lean_explore.models.Base`. We write it directly. |
| b | Pin / existing checkout? | **No.** Only its own `lean/<pkg>/` Lake workspaces at `LATEST`/`TAGGED`; no SHA pinning, no external checkout. Route: **adapter** (decls.jsonl -> SQLite), run embed/index ourselves, describe reimplemented. |
| c | Description reuse key | Skips rows whose `informalization` is non-NULL; cross-DB cache keyed by **(name, source_text)** exactly, discovered by `rglob("lean_explore.db")` under `LEAN_EXPLORE_DATA_DIR` and `LEAN_EXPLORE_CACHE_DIR`. Reuse from upstream/newer data is safe in the "nothing extra emitted" sense (only existing rows are updated); staleness is the risk, handled by hash/name keys in our adapter. |
| d | Dependency context in prompts | Yes: up to **20** dependencies, each description truncated to **256** chars, as a "Dependencies:" block the prompt tells the model to use for understanding only. Processed in Kahn layers so deps are described first. Template in `prompt.txt` (verbatim copy here). |
| e | Index directory selection | `Config.ACTIVE_DATA_PATH` resolved **once at import**: `LEAN_EXPLORE_DATA_DIR` itself if it holds `lean_explore.db`, else newest complete `YYYYMMDD_HHMMSS` subdir. One pin per process via the env var; several pins per process need a subclass (done in `server.py`). HEAD adds a `data_directory=` constructor argument. |
| f | Models; revisions; reranker locality; `rerank_top` | `Qwen/Qwen3-Embedding-0.6B` (1024-d, L2-normalised) and `Qwen/Qwen3-Reranker-0.6B`, both loaded **locally** (sentence-transformers / transformers); no `revision` argument, so we pin by passing a `snapshot_download(revision=sha)` path. Reranker scores are deterministic float32 on CPU. `rerank_top` is a `search()` argument (default 25 engine / 50 Service); 0 disables. |
| g | Prebuilt data | Yes: R2 bucket, version `20260714_172516` (2026-07-14): 408,970 rows = Mathlib **302,546** at commit `aae61ae084c2` (Lean **v4.32.0**) + Init/Std/Lean/Batteries + PhysLean/FLT/formal-conjectures/cslib; all described and embedded. 3.7 GB (db 2.18 GB, FAISS 1.68 GB). **Downloaded** to `~/.local/share/tauceti-explore/upstream/20260714_172516/`. Older than our pins (Mathlib `5e0c4e5239cb`, v4.35.0-rc3), so it seeds most Mathlib names but not the newest ~10k. |
| h | OpenRouter configuration | `OPENROUTER_API_KEY` env var, hard-coded base URL `https://openrouter.ai/api/v1` through `openai.AsyncOpenAI`; `--informalize-model` (default `google/gemini-3-flash-preview`), `--informalize-limit`, `--informalize-max-concurrent` (10), `temperature=0.3`, tenacity retry 3x exp backoff. A vLLM/OpenAI-compatible endpoint needs a **one-line patch** upstream (base URL is not configurable); our describe stage has `--base-url` / `OPENROUTER_BASE_URL`. **1.3.0's wheel does not ship `prompt.txt`**: the stock describe stage crashes with FileNotFoundError on a PyPI install (fixed at HEAD via package-data). |
| i | Python compatibility | **Python 3.14.4 works** (torch 2.14.1+cu130, transformers 5.19.0, sentence-transformers 6.1.0, faiss-cpu 1.15.1, numpy 2.5.3). Caveat: uv must be stopped from back-solving to 2022-era wheels (floors in `pyproject.toml`). 3.12 also resolves (dry run). |

## a. Input format and schema

Read: `extract/doc_parser.py` (1.3.0; HEAD: `extract/docgen/{parser,sqlite_reader,bmp_reader,writer}.py`),
`models/search_db.py`, `extract/types.py`.

* `_detect_docgen_format(workspace)` (HEAD `docgen/parser.py:47`) returns `"sqlite"` when
  `<ws>/.lake/build/api-docs.db` exists and `validate_docgen_sqlite` finds tables
  `{name_info, declaration_ranges, modules}`; else `"bmp"` when `.lake/build/doc-data/**/*.bmp`
  exist. So doc-gen4 >= v4.29.0-rc2 (`api-docs.db`) is the primary path; BMP is legacy. The
  SQLite query (`sqlite_reader._DECLARATIONS_QUERY`) joins `name_info` (module_name, position,
  kind, name, type BLOB, render) with `declaration_ranges`, `declaration_markdown_docstrings`
  (text), `declaration_verso_docstrings` (detected, not decoded) and `modules.source_url`,
  `WHERE n.render = 1`. Rows ending in `.mk` are dropped; the source text is read from the local
  checkout by the `source_url#Lstart-Lend` range; `dependencies` are the `const` names walked out
  of the `name_info.type` RenderedCode BLOB (`rendered_code.extract_names_from_rendered_code`),
  minus self-references. `projection_filter` then drops `toFoo` projections sharing source
  text with their parent.
* **The database schema** (`models/search_db.py`, confirmed identical in the downloaded upstream
  `lean_explore.db`):

  ```sql
  CREATE TABLE declarations (
      id INTEGER NOT NULL PRIMARY KEY,
      name TEXT NOT NULL,            -- unique index ix_declarations_name
      module TEXT NOT NULL,          -- index ix_declarations_module
      docstring TEXT,
      source_text TEXT NOT NULL,     -- Lean source of the declaration
      source_link TEXT NOT NULL,     -- GitHub URL with #Lx-Ly
      dependencies TEXT,             -- JSON array of names, or NULL
      informalization TEXT,          -- the LLM description ("**Name.** text")
      informalization_embedding BLOB -- 1024 x float32, little-endian struct.pack
  );
  ```
  Dependencies are a JSON list of fully-qualified names in `dependencies` (no join table). The
  writer uses `INSERT ... ON CONFLICT(name) DO NOTHING`. Everything else the engine needs lives
  beside the DB as files: `informalization_faiss.index` + `informalization_faiss_ids_map.json`
  (position -> `declarations.id`), `bm25_name_raw/`, `bm25_name_spaced/` (bm25s saves) and
  `bm25_ids_map.json`.
* **Bypass verdict: yes.** `build_explore.py --stage adapt` creates the table from
  `lean_explore.models.Base.metadata.create_all` (so it cannot drift from the pinned version) and
  inserts one row per decls.jsonl record: `source_text` = S0's `signature` (wrapped as
  `<kind> <name> ...` if S0 omits the keyword), `source_link` built from `file`/`line` and the
  pin's SHAs, `dependencies` from a `deps` field when S0 provides one, else inferred by matching
  identifiers in the signature against the extraction (documented approximation; see README
  open questions). A side table `tauceti_meta(name, kind, file, line, is_mathlib, hash,
  description_source, description_model, embedding_source)` keeps what LeanExplore's schema has
  no column for; the engine ignores it.

## b. Pinning and workspaces

Read: `extract/package_config.py`, `extract/package_registry.py`, `extract/package_utils.py`,
`extract/github.py`, `extract/doc_gen4.py` (HEAD `docgen/build.py`, `packages/*`).

`VersionStrategy` is `LATEST` (fetch `lean-toolchain` from `main`/`master` over raw.githubusercontent)
or `TAGGED` (newest semver tag via the GitHub API). `_setup_workspace` writes the toolchain into
`lean/<pkg>/lean-toolchain` and rewrites the doc-gen4 `require` to the toolchain version; `lake
update` then resolves whatever the workspace lakefile requires (the comment in `build.py:161`
admits `git_ref` "is only reported; it is not written into the lakefile"). There is no option for
an arbitrary SHA, no option to point at an existing checkout (`WORKSPACES_ROOT = Path("lean")`
relative to CWD; `LEAN_EXPLORE_PACKAGES_ROOT` only moves that root), and the registry is a Python
dict with no TauCeti entry. Using the native path would mean: a fork adding a `tauceti` package
with a bespoke `TauCetiExtract` wrapper library, a lakefile pinned by hand to the SHA, a
doc-gen4 build in their workspace (hours), and still the parse stage's GitHub-link-to-local-path
heuristics. S0 already produces `decls.jsonl` from `api-docs.db` for every tool, so the native
path would duplicate that build per pin.

**Decision: adapter route.** decls.jsonl -> LeanExplore schema (adapt), then describe (ours),
embed (their `EmbeddingClient`), index (their BM25 builder; FAISS replicated with a flat-index
branch), served by their `SearchEngine`. What we give up: nothing the engine uses; the only
upstream-only artefacts are the GitHub source links, which we synthesise from `file`/`line`.

## c. Description reuse

Read: `extract/informalize.py` (1.3.0), HEAD `extract/informalize/{pipeline,generation}.py`,
`extract/_cache.py`.

* `_get_declarations_to_process` selects `informalization IS NULL`; described rows are never
  re-described, regardless of content (**keyed by presence, not hash**).
* Before any API call, `_discover_database_files()` globs `lean_explore.db` under
  `Config.DATA_DIRECTORY` and `Config.CACHE_DIRECTORY`, and `_load_cache_from_databases` builds
  `{(name, source_text): informalization}`. `_apply_cache_to_declarations` fills exact matches
  (phase 1), then only the misses go to the LLM. So reuse is keyed by **name AND the full source
  text**, which for doc-gen4 input includes the proof body: any proof edit invalidates the
  cache; a renamed declaration misses even if unchanged.
* Reusing descriptions from a newer pin or from the prebuilt data for an older pin: the cache
  only ever `UPDATE`s rows already in the target DB, so nothing is emitted that is not in the
  older extraction. The failure mode is a stale description if the statement changed under the
  same name; with their key that cannot happen (source text must match), with a name key it can.
  Our adapter therefore reuses in this order: (name, source_text) exact; S0 `hash` (statement +
  docstring identity, available when the source is one of our pins); name only, for the upstream
  prebuilt DB whose `source_text` convention differs (optionally gated on equal docstrings with
  `--reuse-strict`). Provenance is recorded per row in `tauceti_meta.description_source`.
  Measured on the slice: 130/130 Mathlib names present upstream were reused by name.
* Embeddings are reused the same way (`extract/embeddings.py`, key = identical informalization
  text), so a reused description also brings its vector for free. We copy the vector with the
  description.

## d. Dependency context

Read: `extract/informalize.py:283-308` (1.3.0) / HEAD `informalize/generation.py:23-98`,
`extract/prompt.txt`.

`_process_one_declaration` takes `dependencies[:20]`, looks each up in `informalizations_by_name`
(everything already described in the DB plus earlier layers of this run), truncates each
description to 256 chars (`[:253] + "..."`), and renders `"Dependencies:\n- Name: desc\n..."`
into the `{dependencies}` slot. Undescribed dependencies are silently omitted. Layers come from
`_build_dependency_layers` (Kahn over the in-DB dependency graph; cycles become a last layer).
`temperature=0.3`, no `max_tokens`. The single prompt template (user role only; verbatim in
`./prompt.txt`) asks for `**{informal_name}.** {informal_description}`, rigorous and
equivalent to the statement, no Lean syntax, docstring and source as the only basis, the
dependency block "for general understanding, not direct translation", and a special rule for
attribute-generated declarations whose name differs from the source. `build_explore.py`
reproduces all of this; the slice prompt averages ~1,040 tokens (chars/3.4) with mostly empty
dependency blocks, more with dense Mathlib context.

## e. Index selection

Read: `config.py`, `search/engine.py:53-125` (1.3.0), HEAD `search/indexes.py`.

`Config` is a class whose attributes are evaluated at import: `DATA_DIRECTORY =
$LEAN_EXPLORE_DATA_DIR or <repo>/data`; `ACTIVE_DATA_PATH = _resolve_active_data_path(...)`:
the data directory itself if it contains `lean_explore.db`, else the newest `YYYYMMDD_HHMMSS`
subdirectory that passes `_is_complete_extraction` (all six artefacts present), else
`DATA_DIRECTORY/ACTIVE_VERSION`. `SearchEngine(use_local_data=True)` reads
`Config.ACTIVE_DATA_PATH` for the FAISS and BM25 paths (FAISS paths are overridable arguments,
BM25 paths are not) and `Config.EXTRACTION_DATABASE_URL` for the DB (overridable via `db_url`).
Consequences: one pin per process works by exporting `LEAN_EXPLORE_DATA_DIR=<pin dir>` before
import; several pins in one process (sharing the two 1.2 GB models) need a subclass that sets
`_bm25_*_path` before `_validate_paths()` runs. `server.py` does that
(`PinnedSearchEngine`); HEAD's `data_directory=` argument would make it unnecessary when a
release ships it. Timestamp-named directories are not required for our layout because we never
rely on "latest".

## f. Models

Read: `util/embedding_client.py`, `util/reranker_client.py`, `search/engine.py:127-149`,
HF API for both repos, upstream `lean_explore.db`.

* Embedder `Qwen/Qwen3-Embedding-0.6B` via `SentenceTransformer(model_name, device)`, `max_seq_length
  512`, documents encoded without prompt, queries with `prompt_name="query"`; the ST pipeline is
  Transformer -> last-token Pooling -> Normalize (checked in the snapshot's `modules.json`), so
  stored vectors are unit length (upstream DB: 1024-d, norm 1.0). Current HF revision
  `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` (2026-04-20), 1.19 GB safetensors.
* Reranker `Qwen/Qwen3-Reranker-0.6B` via `AutoModelForCausalLM` (float32 on CPU, float16 on
  CUDA), `max_length 256` in the engine, instruction "Find relevant Lean 4 math declarations",
  score = softmax over the `true`/`false` next-token logits; runs **in process**, never remote.
  Revision `e61197ed45024b0ed8a2d74b80b4d909f1255473` (2026-04-16).
* Neither client accepts `revision`; we pin by resolving the revision with
  `huggingface_hub.snapshot_download(repo, revision=sha)` and passing the local path as
  `model_name` (both clients accept a path). The service runs with `HF_HUB_OFFLINE=1`.
* `rerank_top`: `SearchEngine.search(..., rerank_top=25)` (Service default 50, MCP uses its
  own); `0`/`None` skips the cross-encoder. Pipeline: BM25 names (two tokenisations) + FAISS
  (`nprobe=64` on IVF) -> RRF (1/rank + 1/rank) -> dependency boost over the top 500 ->
  cross-encoder on top `rerank_top` with final score `1.0*rerank + 0.4*bm25(informalisation) +
  0.2*log-dep + 1.0*fuzzy(name) if fuzzy>=0.7` -> drop `.mk` -> `limit`. All deterministic for a
  fixed index; `server.py` exposes `--rerank-top` (default 25) and returns that final score.

## g. Prebuilt data

Read: `cli/data_commands.py`, `config.py` (`R2_ASSETS_BASE_URL`), the bucket itself.

`lean-explore data fetch` reads `assets/latest.txt` = `20260714_172516` and downloads the six
artefacts from `https://pub-48b75babc4664808b15520033423c765.r2.dev/assets/<version>/`
(`manifest.json` in the bucket only describes the old 0.x layouts). Sizes: `lean_explore.db`
2,176,835,584 B, `informalization_faiss.index` 1,681,035,475 B (IVF over 408,970 x 1024
float32), two id maps 3.2 MB each, BM25 dirs ~26 MB; total 3.7 GB, downloaded and verified
readable. Contents: Mathlib 302,546 rows at commit `aae61ae084c21995ee248964a81e3750ad0db2db`
(core links say `v4.32.0`), Init 32,256, Std 27,467, Lean 19,423, Batteries 3,411, plus
PhysLean 9,391, QuantumInfo 2,194, FormalConjectures 4,605 (+782), FLT 3,834, Cslib 3,061. Every
row has a description (mean 283 chars, median 255, p90 456) and an embedding; `dependencies`
present on 96% of rows (mean 3.6). No TauCeti. Seeding: our b3 pin's Mathlib (`5e0c4e5239cb`,
Sept 2026) is ~2 months newer than the prebuilt (July 2026); expect roughly 95% of Mathlib
names to match by name and a small renamed/added remainder to describe (the dry run reports
the exact count once S0's decls.jsonl exists).

## h. OpenRouter

Read: `util/openrouter_client.py`, `extract/__main__.py:163-170, 256-272`, `extract/informalize.py`.

`OpenRouterClient()` reads `OPENROUTER_API_KEY` (raises if unset), builds
`AsyncOpenAI(base_url="https://openrouter.ai/api/v1")`, `generate()` wraps
`chat.completions.create` with `tenacity.retry(stop_after_attempt(3), wait_exponential(2..10))`.
The pipeline checks the key before any stage runs when `--informalize` is on. CLI knobs:
`--informalize-model` (default `google/gemini-3-flash-preview`), `--informalize-max-concurrent`
(10; a semaphore around the call), `--informalize-limit N` (SQL `LIMIT N` on the NULL rows, so it
is "first N by id", not stratified), commit batch 1000. No cost accounting, no token logging,
no base-URL option: a local vLLM/OpenAI-compatible server needs a one-line patch of the base URL
(or `OPENROUTER_BASE_URL` support). Our describe stage takes `--base-url`/`OPENROUTER_BASE_URL`,
logs `usage` per call to `describe-log.jsonl`, and refuses without the key and
`--i-approve-spend`. Packaging bug: 1.3.0's sdist/wheel omit `extract/prompt.txt` (no
`package-data` entry at `28eac03`), so `informalize_declarations` raises `FileNotFoundError`
on a clean install; HEAD moved it to `extract/informalize/prompt.txt` and declared it.

## i. Python compatibility

`uv pip install --dry-run 'lean-explore[local]==1.3.0'` resolves on both 3.14 and 3.12 to the
same set (torch 2.14.1, transformers 5.19.0, sentence-transformers 6.1.0, faiss-cpu 1.15.1,
numpy 2.5.3). A first `uv sync` with an explicit PyTorch CPU index found no cp314 torch wheel
there and back-solved to sentence-transformers 2.2.2 / transformers 4.12.2 / tokenizers 0.10.3
(a Rust build from source, which fails). Fix: PyPI torch (CUDA build; works on CPU and will use
the GPU once the driver is repaired) plus floors `sentence-transformers>=5`, `transformers>=4.51`,
`torch>=2.6`. On the host, 3.14.4 imports torch, faiss, lean_explore and runs the embedder and
engine; `.python-version` pins 3.14 and `uv.lock` is committed. Note `torch.cuda.is_available()`
reports True even though `nvidia-smi` fails, so every entry point here passes `--device cpu` and
the unit sets `CUDA_VISIBLE_DEVICES=`.
