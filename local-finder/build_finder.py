#!/usr/bin/env python3
"""Build the Lean Finder index for one TauCeti index pin.

    build_finder.py b3-11bc13335fb9 [--device cuda:0|cpu] [--limit N] [--dry-run]

Reads   ~/.local/share/tauceti-index/<pin>/decls.jsonl   (shared extraction; see workboots-plan.md)
Writes  ~/.local/share/tauceti-finder/<pin>/corpus.jsonl  (Lean Finder corpus schema, one row per decl)
        ~/.local/share/tauceti-finder/<pin>/corpus.index  (FAISS IndexFlatIP, same row order)
        ~/.local/share/tauceti-finder/<pin>/MANIFEST.json
and a copy of the manifest under ./manifests/<pin>.json.

Embeddings are produced with Lean Finder's own entry formatting and pooling
(vendor/lean_finder, see finder_common.py) and cached by sha256 of the formatted
entry under ~/.local/share/tauceti-finder/cache/, so a new pin only embeds the
declarations whose text changed.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from finder_common import (
    DATA_ROOT, EMBEDDING_DIM, INDEX_ROOT, LEAN_FINDER_COMMIT, MAX_LENGTH, ROOT,
    Embedder, EmbeddingCache, adapt_decl, default_model_dir, device_kind, entry_key,
    format_entry, package_versions, read_jsonl, resolve_device, sha256_file,
)
from fetch_model import MODEL_REPO, MODEL_REVISION

# Throughput priors for --dry-run, in passage tokens per second through the 8B encoder.
# cpu-float32 is the value measured in VALIDATION.md (128 threads, host load average ~150 from other jobs, so a floor).
# cuda-bfloat16 is an estimate for one RTX Pro 6000 until the first GPU build records it
# (every build writes its measured rate into MANIFEST.json["timing"]).
TOKENS_PER_SEC = {
    "cuda-bfloat16": 6000.0,
    "cuda-float32": 2000.0,
    "cpu-float32": 30.0,
    "cpu-bfloat16": 10.0,
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pin", help="index pin, e.g. b3-11bc13335fb9 (names the input and output directories)")
    ap.add_argument("--decls", type=Path, default=None, help="override the decls.jsonl path")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="override the output directory (default: ~/.local/share/tauceti-finder/<pin>/, "
                         "or .../slices/<pin>-<device>-limit<N>/ when --limit/--stride is used)")
    ap.add_argument("--model-dir", type=Path, default=None, help="pinned model snapshot (default: fetch_model.model_dir())")
    ap.add_argument("--device", default=None, help="cuda:0 | cpu (default: cuda:0 if available else cpu)")
    ap.add_argument("--dtype", choices=["bfloat16", "float32"], default=None,
                    help="model dtype (default: bfloat16 on cuda as upstream; float32 on cpu)")
    ap.add_argument("--batch-size", type=int, default=32, help="upstream default 32")
    ap.add_argument("--max-batch-tokens", type=int, default=32768,
                    help="shrink a batch when its padded size would exceed this many tokens")
    ap.add_argument("--limit", type=int, default=None, help="only the first N rows (after --stride)")
    ap.add_argument("--stride", type=int, default=1, help="take every S-th row (for spread-out slices)")
    ap.add_argument("--dry-run", action="store_true", help="report counts and estimated time; embed nothing")
    ap.add_argument("--no-cache", action="store_true", help="ignore and do not write the embedding cache")
    ap.add_argument("--tokens-per-sec", type=float, default=None, help="override the --dry-run throughput prior")
    ap.add_argument("--log-every", type=int, default=20)
    ap.add_argument("--manifest-copy-dir", type=Path, default=ROOT / "manifests",
                    help="where to copy MANIFEST.json ('' to disable)")
    return ap.parse_args(argv)


def pick_batches(order: list[int], lengths: list[int], batch_size: int, max_tokens: int) -> list[list[int]]:
    """Length-sorted batches (longest first) capped by batch_size and padded-token budget."""
    batches: list[list[int]] = []
    cur: list[int] = []
    cur_max = 0
    for i in order:
        L = lengths[i]
        new_max = max(cur_max, L)
        if cur and (len(cur) >= batch_size or new_max * (len(cur) + 1) > max_tokens):
            batches.append(cur)
            cur, cur_max = [], 0
            new_max = L
        cur.append(i)
        cur_max = new_max
    if cur:
        batches.append(cur)
    return batches


def main(argv=None) -> int:
    args = parse_args(argv)
    t_start = time.time()

    device = resolve_device(args.device)
    dkind = device_kind(device)
    dtype = args.dtype or ("bfloat16" if dkind == "cuda" else "float32")
    model_dir = args.model_dir or default_model_dir()
    if not (model_dir / "config.json").exists():
        log(f"model snapshot missing at {model_dir}; run fetch_model.py first")
        return 2

    decls_path = args.decls or (INDEX_ROOT / args.pin / "decls.jsonl")
    if not decls_path.exists():
        log(f"no extraction at {decls_path}")
        return 2
    extraction_manifest_path = decls_path.parent / "MANIFEST.json"
    extraction_manifest = None
    if extraction_manifest_path.exists():
        try:
            extraction_manifest = json.loads(extraction_manifest_path.read_text())
        except Exception as e:  # noqa: BLE001
            log(f"warning: cannot parse {extraction_manifest_path}: {e}")

    sliced = args.limit is not None or args.stride > 1
    if args.out_dir is not None:
        out_dir = args.out_dir
    elif sliced:
        tag = f"{args.pin}-{dkind}" + (f"-stride{args.stride}" if args.stride > 1 else "") + (f"-limit{args.limit}" if args.limit else "")
        out_dir = DATA_ROOT / "slices" / tag
    else:
        out_dir = DATA_ROOT / args.pin

    # ---- read + adapt -------------------------------------------------------
    log(f"reading {decls_path} (limit={args.limit}, stride={args.stride})")
    decls = read_jsonl(decls_path, limit=args.limit, stride=args.stride)
    corpus = [adapt_decl(d) for d in decls]
    texts = [format_entry(row) for row in corpus]
    keys = [entry_key(t) for t in texts]
    n = len(corpus)
    kinds: dict[str, int] = {}
    unknown_kinds: dict[str, int] = {}
    for row in corpus:
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
        if row["kind"] not in ("theorem", "definition", "inductive", "opaque", "axiom"):
            unknown_kinds[row["kind"]] = unknown_kinds.get(row["kind"], 0) + 1
    type_from: dict[str, int] = {}
    for row in corpus:
        type_from[row["type_from"]] = type_from.get(row["type_from"], 0) + 1
    log(f"{n} declarations; kinds={kinds}; typeFull source={type_from}")
    if type_from.get("typeFull", 0) < n:
        log("warning: some rows have no exact `typeFull` and are rebuilt from the doc-gen4 signature, which only "
            "approximates Lean Finder's text (FORMAT-CHECK.md); run scripts/dump_typefull.lean and "
            "scripts/merge_typefull.py to get exact ppExpr types")
    if unknown_kinds:
        log(f"warning: kinds outside the upstream vocabulary: {unknown_kinds}")
    if n == 0:
        log("nothing to do")
        return 2

    # ---- cache lookup -------------------------------------------------------
    cache = None if args.no_cache else EmbeddingCache(MODEL_REVISION, device, dtype)
    if cache is not None:
        vecs, hit = cache.gather(keys)
        log(f"cache {cache.dir}: {len(cache)} entries; hits {int(hit.sum())}/{n}")
    else:
        vecs, hit = np.zeros((n, EMBEDDING_DIM), dtype=np.float32), np.zeros(n, dtype=bool)
    misses = [i for i in range(n) if not hit[i]]

    # ---- dry run ------------------------------------------------------------
    if args.dry_run:
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(str(model_dir), padding_side="left", fix_mistral_regex=True)
        sample_idx = misses if len(misses) <= 5000 else random.Random(0).sample(misses, 5000)
        enc = tok([texts[i] for i in sample_idx], padding=False, truncation=True, max_length=MAX_LENGTH)
        lens = [len(x) for x in enc["input_ids"]] or [0]
        mean_tokens = float(np.mean(lens))
        total_tokens = mean_tokens * len(misses)
        rate = args.tokens_per_sec or TOKENS_PER_SEC.get(f"{dkind}-{dtype}", 100.0)
        est = total_tokens / rate if rate else float("inf")
        report = {
            "pin": args.pin, "decls": str(decls_path), "out_dir": str(out_dir),
            "device": device, "dtype": dtype, "rows": n, "kinds": kinds,
            "cache_hits": n - len(misses), "to_embed": len(misses),
            "tokens": {"sampled": len(lens), "mean": round(mean_tokens, 1), "median": float(np.median(lens)),
                       "p95": float(np.percentile(lens, 95)), "max": int(max(lens)),
                       "truncated_at_max_length": int(sum(1 for L in lens if L >= MAX_LENGTH)),
                       "estimated_total": int(total_tokens)},
            "assumed_tokens_per_sec": rate,
            "estimated_embed_seconds": round(est, 1),
            "estimated_embed_human": f"{est/3600:.2f} h" if est > 3600 else f"{est/60:.1f} min",
            "index_bytes": n * EMBEDDING_DIM * 4,
            "model_load_not_included": True,
        }
        print(json.dumps(report, indent=1))
        return 0

    # ---- embed misses -------------------------------------------------------
    timing: dict = {"started_at": utc_now()}
    embedder = None
    if misses:
        log(f"loading model {model_dir} on {device} ({dtype}) ...")
        embedder = Embedder(model_dir, device=device, dtype=dtype)
        timing["model_load_seconds"] = round(embedder.load_seconds, 1)
        log(f"model ready in {embedder.load_seconds:.0f}s; hidden_size={embedder.dim}")
        if embedder.dim != EMBEDDING_DIM:
            log(f"unexpected embedding dim {embedder.dim}")
            return 3
        lengths_sub = embedder.token_lengths([texts[i] for i in misses])
        lengths = {i: L for i, L in zip(misses, lengths_sub)}
        order = sorted(misses, key=lambda i: -lengths[i])
        batches = pick_batches(order, {i: lengths[i] for i in misses}, args.batch_size, args.max_batch_tokens)
        total_tokens = sum(lengths.values())
        log(f"embedding {len(misses)} entries, {total_tokens} tokens, {len(batches)} batches "
            f"(batch<= {args.batch_size}, <= {args.max_batch_tokens} padded tokens)")
        t0 = time.time()
        done_tokens = 0
        for b, batch in enumerate(batches):
            out = embedder.embed([texts[i] for i in batch])
            vecs[batch] = out
            hit[batch] = True
            if cache is not None:
                cache.put_many([keys[i] for i in batch], out)
            done_tokens += sum(lengths[i] for i in batch)
            if (b + 1) % args.log_every == 0 or b == len(batches) - 1:
                el = time.time() - t0
                rate = done_tokens / el if el > 0 else 0
                eta = (total_tokens - done_tokens) / rate if rate > 0 else 0
                log(f"batch {b+1}/{len(batches)} | {rate:.0f} tok/s | ETA {eta/60:.1f} min")
                if cache is not None:
                    cache.flush()
        el = time.time() - t0
        timing.update({"embed_seconds": round(el, 1), "embedded_rows": len(misses), "embedded_tokens": total_tokens,
                       "tokens_per_sec": round(total_tokens / el, 1) if el > 0 else None,
                       "rows_per_sec": round(len(misses) / el, 2) if el > 0 else None})
        if cache is not None:
            cache.close()
    assert bool(hit.all()), "internal error: rows without vectors"
    norms = np.linalg.norm(vecs, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-3):
        log(f"warning: {int((np.abs(norms-1) > 1e-3).sum())} vectors are not unit length")

    # ---- write outputs ------------------------------------------------------
    import faiss
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus_path, index_path, manifest_path = out_dir / "corpus.jsonl", out_dir / "corpus.index", out_dir / "MANIFEST.json"
    atomic_write_bytes(corpus_path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in corpus).encode("utf-8"))
    index = faiss.IndexFlatIP(EMBEDDING_DIM)           # upstream build_index.py:90
    index.add(np.ascontiguousarray(vecs, dtype=np.float32))
    tmp_index = index_path.with_suffix(".index.tmp")
    faiss.write_index(index, str(tmp_index))
    os.replace(tmp_index, index_path)
    timing["finished_at"] = utc_now()
    timing["total_seconds"] = round(time.time() - t_start, 1)

    manifest = {
        "tool": "finder",
        "pin": args.pin,
        # identity fields copied verbatim from the extraction (S0) manifest; the bench checks these
        **{k: (extraction_manifest or {}).get(k) for k in ("tauceti_sha", "mathlib_rev", "lean_toolchain", "decls_sha256")},
        "built_at": timing["finished_at"],
        "device": device,
        "dtype": dtype,
        "rows": n,
        "embedding_dim": EMBEDDING_DIM,
        "kinds": kinds,
        "type_full_source": type_from,
        "model": {"repo": MODEL_REPO, "revision": MODEL_REVISION, "dir": str(model_dir)},
        "lean_finder_commit": LEAN_FINDER_COMMIT,
        "files": {
            "corpus.jsonl": {"sha256": sha256_file(corpus_path), "bytes": corpus_path.stat().st_size},
            "corpus.index": {"sha256": sha256_file(index_path), "bytes": index_path.stat().st_size,
                             "type": "faiss.IndexFlatIP", "metric": "inner product on L2-normalised vectors"},
        },
        "source": {
            "decls": str(decls_path),
            "decls_sha256": sha256_file(decls_path),
            "extraction_manifest": extraction_manifest,
        },
        "slice": ({"limit": args.limit, "stride": args.stride} if sliced else None),
        "embedding": {
            "entry_format": "vendor/lean_finder/build_index.py:format_entry (docstring None -> 'None')",
            "pooling": "last_token_pool, left padding, L2-normalised fp32",
            "max_length": MAX_LENGTH,
            "batch_size": args.batch_size,
            "max_batch_tokens": args.max_batch_tokens,
            "length_sorted_batches": True,
            "cache_dir": (str(cache.dir) if cache is not None else None),
            "cache_hits": n - len(misses),
            "cache_misses": len(misses),
        },
        "timing": timing,
        "versions": package_versions(),
    }
    atomic_write_bytes(manifest_path, (json.dumps(manifest, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    if args.manifest_copy_dir and str(args.manifest_copy_dir):
        args.manifest_copy_dir.mkdir(parents=True, exist_ok=True)
        copy_name = f"{args.pin}.json" if not sliced else f"{out_dir.name}.json"
        (args.manifest_copy_dir / copy_name).write_bytes(manifest_path.read_bytes())
    log(f"wrote {corpus_path}, {index_path} ({index_path.stat().st_size/1e6:.1f} MB), {manifest_path}")
    print(json.dumps({"out_dir": str(out_dir), "rows": n, "embedded": len(misses), "timing": timing}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
