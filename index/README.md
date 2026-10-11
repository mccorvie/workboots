# TauCeti index pins and shared declaration extraction (workstream S0)

One frozen declaration extraction per corpus batch, consumed by the Lean Finder and LeanExplore
service builders (`~/lean/workboots/local-finder/`, `~/lean/workboots/local-explore/`). Contract:
`workboots-plan.md`, sections "Decisions" (2, 4), "Index artefacts".

## Pins

`pins.py` derives them from the bench corpus index (`~/tauceti-bench-data/index/records-latest/admitted.jsonl`)
and the read-only TauCeti mirror (`/srv/tauceti/records/git/TauCeti.git`): per batch, keep the `fix`
captures on the batch's majority toolchain, take the oldest merge base, verify with
`git merge-base --is-ancestor` that it is an ancestor of every other merge base.

| pin | TauCeti | committed | toolchain | Mathlib | captures / merge bases | excluded |
|---|---|---|---|---|---|---|
| `b2-b5500936b563` | `b5500936b563898560631cc385ade419f1988dab` | 2026-09-06T05:04:12Z | `leanprover/lean4:v4.34.0-rc2` | `5fcc6656691ed31965746c369f41fa75e567ac9d` | 336 / 62 | 2 rc1 captures (`tc-ad5d6e13bd780388b5910670`, `tc-e6ffa839b204741a5f30c1be`, merge base `ee6673df`) |
| `b3-11bc13335fb9` | `11bc13335fb906290a0bcbe57747f20cd21dbf2c` | 2026-09-26T21:38:23Z | `leanprover/lean4:v4.35.0-rc3` | `5e0c4e5239cb0a2d86d68a884bf52cfd963fce22` | 138 / 86 | none |

`python3 pins.py` prints one line per batch; `--json` prints the full derivation (including the
excluded capture ids, which the bench must also exclude from b2 selections).

## Route: doc-gen4 `docInfo` facet (primary route; no fallback was needed)

TauCeti already ships the nested docs project the plan asks for: `docbuild/lakefile.toml` requires
`doc-gen4` (kim-em fork `4cac5a75c52048204e6d015554fbc10ce3f269a2`, "interproject linking", =
upstream `v4.33.0-rc1` + 2 commits; upstream `v4.34.0-rc2`/`v4.35.0-rc3` differ from it only by
toolchain bumps, one atomic-HTML-write fix and one docstring-spelling fix, none of which touch the
`docInfo` data) plus TauCeti by path, with `packagesDir = ../.lake/packages` so Mathlib is shared with
the parent checkout. That fork compiles unchanged on both v4.34.0-rc2 and v4.35.0-rc3 (CI verifies the
same thing with `lake build doc-gen4` every three hours), so the extraction uses exactly the doc-gen4
that renders TauCeti's published API docs.

doc-gen4's `docInfo` **module facet** runs `doc-gen4 single --build <docbuild/.lake/build> <module>
api-docs.db <srcUri>` for a module after doing the same for every import (recursively) and after
`genCore` for `Init`, `Std`, `Lake`, `Lean`. All of it writes into the **one SQLite file**
`docbuild/.lake/build/api-docs.db` (the root package's build dir), so TauCeti, the whole Mathlib
closure, Batteries/Aesop/Qq/ProofWidgets/Plausible/ImportGraph/LeanSearchClient/Cli and Lean core all
land in the same database from a single `lake build TauCetiDocs:docInfo`. No second build is needed
for Mathlib. To cover *all* of Mathlib (not only the part TauCeti imports) the aggregator root
`docbuild/TauCetiDocs.lean` is generated as `public import Mathlib` plus every `TauCeti.*` module
(TauCeti's own root is intentionally empty; this mirrors `docbuild/mk_docs_root.py` and the Loogle
`TauCeti/LoogleAll.lean` trick). `DOCGEN_LOCAL_MODULE_ROOTS` / `DOCGEN_DEPS_DOCS_URL` only affect the
HTML stage (`fromDb`), which is never run.

`extract.py` (Python 3, stdlib `sqlite3`) reads `name_info` (name, kind, type blob, render flag),
`declaration_args` (binder blobs), `declaration_ranges`, `declaration_markdown_docstrings`,
`definitions`/`opaques`/`axioms`/`inductives` (modifiers) and `structure_parents` (`extends`). Types and
binders are doc-gen4 `RenderedCode` blobs (Lean `TaggedText` with link tags, serialized by
leansqlite's `ToBinary`: varint Nats, length-prefixed UTF-8 strings, tagged unions); the script decodes
them and concatenates the text leaves, so `signature` is the plain text of doc-gen4's
`<div class="decl_header">`: `<kind description> <name> <binders> [extends <parents>] : <type>`, e.g.
`theorem Nat.add_comm (n m : Nat) : n + m = m + n`. Pretty-printer line breaks inside long types are kept.

### Record format (`decls.jsonl`, one JSON object per line)

| field | content |
|---|---|
| `name` | full declaration name |
| `kind` | doc-gen4 `getKind` label: `def`, `theorem`, `instance`, `structure`, `class`, `class inductive`, `inductive`, `axiom`, `opaque`, `ctor` |
| `module` | Lean module name |
| `file` | module path relative to its package root (`TauCeti/...`, `Mathlib/...`, `Init/...`) |
| `line` | 1-based start line, or `null` |
| `signature` | doc-gen4 header as plain text (see above) |
| `docstring` | Markdown docstring or `""` |
| `is_mathlib` | `true` only for `Mathlib.*` modules; TauCeti is `TauCeti.*`; everything else (Lean core, Batteries, ...) is neither |
| `type_full` | `ppExpr` of the constant's type, width 120 (see below); null for 51 b3 rows |
| `hash` | `sha256(name + "\n" + signature + "\n" + docstring)` hex |

Rows kept: every `name_info` row except private names, de-duplicated by name. Constructors and
structure projections (`render = 0` in doc-gen4, shown only on their parent's page) are kept because
a search should find them. Records are sorted by `(module, line, name)`.

`MANIFEST.json`: `batch, pin, tauceti_sha, mathlib_rev, lean_toolchain, docgen4_rev, docgen4_url,
route, api_docs_db_sha256, extractor_sha256, built_at, decls_sha256, decls_size_bytes, counts
{tauceti, mathlib, other, total, by_package}, modules, includes {lean_core, lake, batteries, ...}`.

## Layout

```
~/.local/share/tauceti-index/<pin>/
  TauCeti/                 scratch checkout at the pin (cloned with --reference to the mirror, dissociated)
  TauCeti/docbuild/        TauCeti's nested doc-gen4 project; .lake/build/api-docs.db is the raw output
  decls.jsonl  MANIFEST.json
  timings.tsv  logs/<stage>.log  lake-cache.toml
~/lean/workboots/index/    pins.py  build_extraction.sh  extract.py  README.md  <pin>/MANIFEST.json (copies)
```

## Adding a pin

1. Add the batch's `worker_sha` prefixes to `BATCHES` in `pins.py` (or export `TAUCETI_SHA` to bypass it).
2. `./build_extraction.sh <pin>` (optionally `SMOKE_MODULE=TauCeti.Topology.CompactOpen` first). Stages: clone, `lake exe cache get` (Mathlib oleans),
   `lake cache get --service tauceti-public --repo TauCetiProject/TauCeti` (TauCeti's own oleans from
   `cache.taucetiproject.org`; a miss means TauCeti is compiled from source), docbuild setup
   (`lean-toolchain` symlink + `sync_manifest.py`, as `.github/workflows/pages.yml` does), `lake build
   doc-gen4` (compatibility check), aggregator, `lake build TauCetiDocs:docInfo`, `extract.py`.
   Each stage is idempotent and appends to `timings.tsv`.
3. Copy `MANIFEST.json` here (the script does) and record the timings below.

If a future toolchain breaks the pinned doc-gen4 fork, edit `docbuild/lakefile.toml` in the scratch
checkout to the upstream tag matching `lean-toolchain` (`https://github.com/leanprover/doc-gen4` @
`vX.Y.Z`) and run `MATHLIB_NO_CACHE_ON_UPDATE=1 lake update doc-gen4` there; the DB schema and
`extract.py` are unchanged across those tags (schema hash is checked by doc-gen4 itself).

## Timings

Stage timings in seconds (host under heavy concurrent load; see `timings.tsv` in each pin dir for the raw rows, including the earlier predecessor smoke runs).

| stage | b2 | b3 |
|---|---|---|
| mathlib-cache | 374 | 214 |
| tauceti-cache | 30 | 33 |
| docgen4-build | 145 | 103 |
| docinfo | 1653 | 1365 |
| extract | 70 | 129 |
| typefull-dump | 2059 | 2809 |
| typefull-merge | 13 | 16 |
| smoke-docinfo | - | 915 |

Totals (build only, warm caches): docInfo 1653 s (b2) / 1365 s (b3), typefull dump 2059 s / 2809 s single-threaded, extract ~70-83 s.
`lake build` here has no `-j`; Lake parallelises by itself. The `docinfo` stage rows add up resumed attempts.

## Result

| pin | records | TauCeti | Mathlib | other | type_full missing |
|---|---|---|---|---|---|
| b2-b5500936b563 | 473,395 | 52,936 | 320,612 | 99,847 | 0 |
| b3-11bc13335fb9 | 510,999 | 87,221 | 324,054 | 99,724 | 51 (`_mkInternal` ctors) |

All of Mathlib is included (aggregator `public import Mathlib`), plus the whole import closure: Lean core (`Init`, `Std`, `Lean`, `Lake`),
Batteries, Aesop, Qq, ProofWidgets, Plausible, ImportGraph, LeanSearchClient. `is_mathlib` is false for those; per-package counts are in
`MANIFEST.json` `counts.by_package`.

## `type_full`

Stage 8 of `build_extraction.sh` runs `typefull/dump_typefull.lean` (a copy of S2's `local-finder/scripts/dump_typefull.lean`; `ppExpr` of each
constant's type at width 120, Lean Finder's training text) under the pin's own toolchain and `LEAN_PATH`, then `typefull/merge_type_full.py` joins it
into `decls.jsonl` by name as `type_full` (null when absent), refreshes `decls_sha256`/size and records `type_full` coverage, dump and merge sha256 in
`MANIFEST.json`. Search services embed `type_full`; `signature` stays for display. Note the field is `type_full`, not S2's `typeFull`.

