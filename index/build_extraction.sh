#!/usr/bin/env bash
# build_extraction.sh <pin> — produce ~/.local/share/tauceti-index/<pin>/{decls.jsonl,MANIFEST.json}
#
# <pin> is `b2-<sha12>` / `b3-<sha12>` as printed by pins.py (`pins.py --json` supplies the full SHA).
# Stages are idempotent and timed into <pin-dir>/timings.tsv (stage, start, end, seconds, rc); logs go
# to <pin-dir>/logs/<stage>.log. Re-running resumes: finished stages are skipped by their own markers.
#
# Route: TauCeti's own nested docbuild/ project (requires doc-gen4 + TauCeti by path, shares the parent's
# .lake/packages) -> `lake build TauCetiDocs:docInfo` with an aggregator root that imports all of Mathlib
# and every TauCeti module -> docbuild/.lake/build/api-docs.db (SQLite) -> extract.py.
#
# Environment overrides:
#   TAUCETI_INDEX_ROOT   default ~/.local/share/tauceti-index
#   TAUCETI_MIRROR       default /srv/tauceti/records/git/TauCeti.git (read-only; used as a clone reference)
#   TAUCETI_REMOTE       default https://github.com/TauCetiProject/TauCeti.git
#   (Lake in this toolchain has no -j flag; it parallelises over all cores by itself)
#                        module's import closure, so do not use all cores on a shared host)
#   SMOKE_MODULE         if set, build only this module's docInfo first (e.g. TauCeti.Topology.CompactOpen)
#   SKIP_FULL            if set to 1, stop after the smoke stage
set -euo pipefail

PIN="${1:?usage: build_extraction.sh <pin>   (e.g. b3-11bc13335fb9)}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${TAUCETI_INDEX_ROOT:-$HOME/.local/share/tauceti-index}"
MIRROR="${TAUCETI_MIRROR:-/srv/tauceti/records/git/TauCeti.git}"
REMOTE="${TAUCETI_REMOTE:-https://github.com/TauCetiProject/TauCeti.git}"
DOCINFO_JOBS="${DOCINFO_JOBS:-48}"
export PATH="$HOME/.elan/bin:$PATH"

PIN_DIR="$ROOT/$PIN"
SRC="$PIN_DIR/TauCeti"
DOCBUILD="$SRC/docbuild"
mkdir -p "$PIN_DIR/logs"

# Resolve the pin to a full SHA + batch via pins.py (needs the corpus index and the mirror). Allow an
# explicit TAUCETI_SHA for pins derived elsewhere.
BATCH="${PIN%%-*}"
if [ -z "${TAUCETI_SHA:-}" ]; then
  TAUCETI_SHA="$(python3 "$HERE/pins.py" --json | python3 -c '
import json,sys; pins=json.load(sys.stdin); pin=sys.argv[1]
m=[p for p in pins.values() if p["pin"]==pin]
if not m: sys.exit(f"pins.py does not know {pin}; set TAUCETI_SHA explicitly")
print(m[0]["tauceti_sha"])' "$PIN")"
fi

stage() {  # stage <name> <cwd> <cmd...>
  local name="$1" cwd="$2"; shift 2
  local log="$PIN_DIR/logs/$name.log"
  local start_iso; start_iso="$(date -u +%FT%TZ)"; local start; start="$(date +%s)"
  local rc=0
  ( cd "$cwd" && "$@" ) >"$log" 2>&1 || rc=$?
  printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$start_iso" "$(date -u +%FT%TZ)" "$(( $(date +%s) - start ))" "$rc" >> "$PIN_DIR/timings.tsv"
  echo "[$PIN] $name: rc=$rc $(( $(date +%s) - start ))s ($log)"
  return $rc
}

# 1. Scratch checkout at the pin (objects borrowed from the local mirror, then dissociated).
if [ ! -d "$SRC/.git" ]; then
  stage clone "$PIN_DIR" git clone --quiet --reference-if-able "$MIRROR" --dissociate "$REMOTE" "$SRC"
fi
if [ "$(git -C "$SRC" rev-parse HEAD)" != "$TAUCETI_SHA" ]; then
  git -C "$SRC" fetch --quiet origin "$TAUCETI_SHA" || git -C "$SRC" fetch --quiet origin
  git -C "$SRC" checkout --quiet --detach "$TAUCETI_SHA"
fi
TOOLCHAIN="$(cat "$SRC/lean-toolchain")"
MATHLIB_REV="$(python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print(next(p["rev"] for p in m["packages"] if p["name"]=="mathlib"))' "$SRC/lake-manifest.json")"
echo "[$PIN] TauCeti $TAUCETI_SHA  $TOOLCHAIN  Mathlib $MATHLIB_REV"

# 2. Mathlib oleans from Mathlib's cache (also clones all dependencies into .lake/packages).
if [ ! -f "$PIN_DIR/.stage-mathlib-cache" ]; then
  stage mathlib-cache "$SRC" lake exe cache get || stage mathlib-cache-retry "$SRC" lake exe cache get
  touch "$PIN_DIR/.stage-mathlib-cache"
fi

# 3. TauCeti's own prebuilt outputs from the public Lake artifact cache (the worker and CI use the same
#    service). A miss is non-fatal: the docInfo build then compiles TauCeti from source.
cat > "$PIN_DIR/lake-cache.toml" <<'TOML'
cache.defaultService = "tauceti-public"
[[cache.service]]
name = "tauceti-public"
kind = "s3"
artifactEndpoint = "https://cache.taucetiproject.org/artifacts"
revisionEndpoint = "https://cache.taucetiproject.org/revisions"
TOML
export LAKE_CACHE_DIR="$SRC/.lake/cache" LAKE_ARTIFACT_CACHE=true LAKE_RESTORE_ARTIFACTS=true
if [ ! -f "$PIN_DIR/.stage-tauceti-cache" ]; then
  if LAKE_CONFIG="$PIN_DIR/lake-cache.toml" stage tauceti-cache "$SRC" \
      lake cache get --service tauceti-public --repo TauCetiProject/TauCeti --max-revs=100; then
    touch "$PIN_DIR/.stage-tauceti-cache"
  else
    echo "[$PIN] warning: TauCeti artifact cache miss or failure; TauCeti will be compiled from source" >&2
  fi
fi

# 4. Nested docbuild project: TauCeti ships one (docbuild/lakefile.toml requires doc-gen4 + TauCeti by
#    path, packagesDir = ../.lake/packages). Mirror the Pages workflow: toolchain symlink, then copy the
#    parent's resolved pins into the docbuild manifest (never `lake update`).
cd "$DOCBUILD"
[ -e lean-toolchain ] || ln -s ../lean-toolchain lean-toolchain
python3 sync_manifest.py >/dev/null
DOCGEN_REV="$(python3 -c 'import json; m=json.load(open("lake-manifest.json")); print(next(p["rev"] for p in m["packages"] if p["name"].strip("«»")=="doc-gen4"))')"
DOCGEN_URL="$(python3 -c 'import json; m=json.load(open("lake-manifest.json")); print(next(p["url"] for p in m["packages"] if p["name"].strip("«»")=="doc-gen4"))')"
export LAKE_NO_CACHE=true   # everything needed was restored above; do not reach for remote caches
if [ ! -f "$PIN_DIR/.stage-docgen4-build" ]; then
  stage docgen4-build "$DOCBUILD" lake build doc-gen4
  touch "$PIN_DIR/.stage-docgen4-build"
fi

# 5. Aggregator root: all of Mathlib plus every TauCeti module (TauCeti's own root is empty by design).
{
  printf 'module\n\n'
  printf 'public import Mathlib\n'
  find "$SRC/TauCeti" -type f -name '*.lean' -print0 | sort -z | while IFS= read -r -d '' f; do
    m="${f#"$SRC/"}"; m="${m%.lean}"; m="${m//\//.}"
    printf 'public import %s\n' "$m"
  done
} > TauCetiDocs.lean
echo "[$PIN] TauCetiDocs.lean imports $(grep -c '^public import' TauCetiDocs.lean) modules"

# 6. Optional smoke slice, then the full docInfo build. Everything lands in the one api-docs.db.
if [ -n "${SMOKE_MODULE:-}" ] && [ ! -f "$PIN_DIR/.stage-smoke-docinfo" ]; then
  stage smoke-docinfo "$DOCBUILD" lake build "$SMOKE_MODULE:docInfo"
  touch "$PIN_DIR/.stage-smoke-docinfo"
fi
if [ "${SKIP_FULL:-0}" = 1 ]; then echo "[$PIN] SKIP_FULL=1, stopping after smoke"; exit 0; fi
if [ ! -f "$PIN_DIR/.stage-docinfo" ]; then
  stage docinfo "$DOCBUILD" lake build TauCetiDocs:docInfo
  touch "$PIN_DIR/.stage-docinfo"
fi
DB="$DOCBUILD/.lake/build/api-docs.db"
[ -s "$DB" ] || { echo "[$PIN] $DB missing" >&2; exit 1; }

# 7. Extraction.
stage extract "$PIN_DIR" python3 -I "$HERE/extract.py" --db "$DB" --out "$PIN_DIR/decls.jsonl" \
  --meta "batch=$BATCH" --meta "pin=$PIN" --meta "tauceti_sha=$TAUCETI_SHA" --meta "mathlib_rev=$MATHLIB_REV" \
  --meta "lean_toolchain=$TOOLCHAIN" --meta "docgen4_rev=$DOCGEN_REV" --meta "docgen4_url=$DOCGEN_URL" \
  --meta "route=doc-gen4 docInfo facet (api-docs.db)" --meta "api_docs_db_sha256=$(sha256sum "$DB" | cut -c1-64)"
tail -1 "$PIN_DIR/logs/extract.log"
# 8. type_full: ppExpr of each constant's type at width 120 (Lean Finder's training text), via a copy of S2's
#    dump_typefull.lean run over the built environment; merged into decls.jsonl by name (skipped if already merged).
if ! grep -q '"type_full"' "$PIN_DIR/MANIFEST.json"; then
  tc="${TOOLCHAIN#leanprover/lean4:}"; lean_home="$HOME/.elan/toolchains/leanprover--lean4---$tc"
  lp=""; for d in "$SRC"/.lake/packages/*/.lake/build/lib/lean; do lp="$lp$d:"; done
  python3 -I -c '
import json,sys
print("\n".join(sorted({json.loads(l)["module"] for l in open(sys.argv[1],encoding="utf-8") if l.strip()} - {""})))' "$PIN_DIR/decls.jsonl" > "$PIN_DIR/typefull.modules"
  LEAN_PATH="$lp$SRC/.lake/build/lib/lean:$lean_home/lib/lean" stage typefull-dump /tmp \
    bash -c "'$lean_home/bin/lean' --run '$HERE/typefull/dump_typefull.lean' '@$PIN_DIR/typefull.modules' > '$PIN_DIR/typefull.jsonl'"
  stage typefull-merge "$PIN_DIR" python3 -I "$HERE/typefull/merge_type_full.py" "$PIN_DIR/decls.jsonl" "$PIN_DIR/typefull.jsonl" "$PIN_DIR/MANIFEST.json" \
    "dump_sha256=$(sha256sum "$HERE/typefull/dump_typefull.lean" | cut -c1-64)" "merge_sha256=$(sha256sum "$HERE/typefull/merge_type_full.py" | cut -c1-64)"
  tail -1 "$PIN_DIR/logs/typefull-merge.log"
fi
mkdir -p "$HERE/$PIN" && cp "$PIN_DIR/MANIFEST.json" "$HERE/$PIN/MANIFEST.json"
echo "[$PIN] done: $PIN_DIR/decls.jsonl  $PIN_DIR/MANIFEST.json  (copy: $HERE/$PIN/MANIFEST.json)"
