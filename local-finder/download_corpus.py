#!/usr/bin/env python3
"""Download Lean Finder's public Mathlib corpora (and optionally their FAISS indices).

Derived from vendor/lean_finder/download_corpus.py (delta-lab-ai/lean-finder @ 69b7646),
with three changes: the revision is pinned to a commit hash, the destination is
~/.local/share/tauceti-finder/upstream-corpus/, and the file list is selectable so the
10 GB of indices are not fetched unless asked for.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

from fetch_model import MODEL_REPO, MODEL_REVISION

DEFAULT_DIR = Path.home() / ".local/share/tauceti-finder/upstream-corpus"
CORPORA = ["mathlib4_19_0.jsonl", "mathlib4_24_0.jsonl", "mathlib4_28_0.jsonl"]
INDICES = ["mathlib4_19_0.index", "mathlib4_24_0.index", "mathlib4_28_0.index"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local-dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--revision", default=MODEL_REVISION)
    ap.add_argument("--files", nargs="*", default=None,
                    help="explicit file list (default: the three corpus JSONLs)")
    ap.add_argument("--with-indices", action="store_true", help="also fetch the three FAISS indices (~10 GB)")
    args = ap.parse_args()

    files = list(args.files) if args.files is not None else list(CORPORA)
    if args.with_indices:
        files += INDICES
    args.local_dir.mkdir(parents=True, exist_ok=True)
    for fn in files:
        print(f"Downloading {fn} -> {args.local_dir}/{fn} ...", flush=True)
        hf_hub_download(repo_id=MODEL_REPO, repo_type="model", revision=args.revision,
                        filename=fn, local_dir=str(args.local_dir))
        print(f"  Saved to {args.local_dir}/{fn}", flush=True)
    (args.local_dir / "PINNED_REVISION").write_text(f"{MODEL_REPO}\n{args.revision}\n")
    print("All downloads complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
