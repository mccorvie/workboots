#!/usr/bin/env python3
"""Cross-check our encoder against Lean Finder's shipped FAISS index.

Takes rows of the upstream corpus, embeds their exact passage text with our
Embedder, and compares with the vectors stored in the upstream IndexFlatIP
(reconstructed by row id).  Reports cosine agreement and whether each of our
vectors retrieves its own row at rank 1 out of the full upstream index.  A
match here validates tokenizer, formatting, pooling and normalisation end to
end (bf16-GPU upstream vs whatever --device/--dtype we run with).

    scripts/check_against_upstream.py --stride 1123 --limit 200 --device cpu --dtype float32
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from finder_common import Embedder, default_model_dir, format_entry, read_jsonl, resolve_device  # noqa: E402

UP = Path.home() / ".local/share/tauceti-finder/upstream-corpus"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", type=Path, default=UP / "mathlib4_28_0.jsonl")
    ap.add_argument("--index", type=Path, default=UP / "mathlib4_28_0.index")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--stride", type=int, default=1123)
    ap.add_argument("--device", default=None)
    ap.add_argument("--dtype", choices=["bfloat16", "float32"], default=None)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--queries", type=Path, default=None, help="optional JSONL of {query, expect} to run against the upstream index")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    import faiss
    device = resolve_device(args.device)
    dtype = args.dtype or ("bfloat16" if device.startswith("cuda") else "float32")
    rows = read_jsonl(args.corpus)
    ids = list(range(0, len(rows), args.stride))[: args.limit]
    texts = [format_entry(rows[i]) for i in ids]
    print(f"loading upstream index {args.index} ...", flush=True)
    index = faiss.read_index(str(args.index))
    assert index.ntotal == len(rows), (index.ntotal, len(rows))
    theirs = np.stack([index.reconstruct(i) for i in ids]).astype(np.float32)

    emb = Embedder(default_model_dir(), device=device, dtype=dtype)
    print(f"model loaded in {emb.load_seconds:.0f}s; embedding {len(texts)} rows on {device}/{dtype}", flush=True)
    lens = emb.token_lengths(texts)
    order = sorted(range(len(texts)), key=lambda j: -lens[j])
    ours = np.zeros_like(theirs)
    t0 = time.time()
    for s in range(0, len(order), args.batch_size):
        b = order[s:s + args.batch_size]
        ours[b] = emb.embed([texts[j] for j in b])
    el = time.time() - t0
    cos = (ours * theirs).sum(1)
    D, I = index.search(ours, 10)
    rank = []
    for j, i in enumerate(ids):
        pos = list(I[j]).index(i) + 1 if i in I[j] else None
        rank.append(pos)
    r1 = sum(1 for r in rank if r == 1)
    r10 = sum(1 for r in rank if r is not None)
    report = {
        "device": device, "dtype": dtype, "rows": len(ids), "tokens": int(sum(lens)),
        "embed_seconds": round(el, 1), "tokens_per_sec": round(sum(lens) / el, 1), "rows_per_sec": round(len(ids) / el, 2),
        "cosine_vs_upstream": {"mean": float(cos.mean()), "min": float(cos.min()), "p05": float(np.percentile(cos, 5)),
                               "median": float(np.median(cos))},
        "self_retrieval": {"rank1": r1, "top10": r10, "of": len(ids)},
        "worst": [{"row": int(ids[j]), "name": rows[ids[j]]["name"], "cos": float(cos[j]), "tokens": lens[j]} for j in np.argsort(cos)[:5]],
    }
    if args.queries:
        qs = read_jsonl(args.queries)
        out = []
        for q in qs:
            t1 = time.time()
            qv = emb.embed_query(q["query"])
            Dq, Iq = index.search(qv, 10)
            names = [rows[int(i)]["name"] for i in Iq[0]]
            out.append({"query": q["query"], "ms": int((time.time() - t1) * 1000), "top10": names,
                        "scores": [round(float(x), 4) for x in Dq[0]],
                        "expect": q.get("expect"), "expect_rank": (names.index(q["expect"]) + 1) if q.get("expect") in names else None})
        report["queries_vs_upstream_index"] = out
    text = json.dumps(report, indent=1, ensure_ascii=False)
    print(text)
    if args.out:
        args.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
