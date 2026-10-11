#!/usr/bin/env python3
"""Download the pinned Lean Finder model snapshot into the local model store.

Pins delta-lab-ai/lean-finder by commit hash (see MODEL_REVISION) and skips
the corpus/index files that share the same HF repo (use download_corpus.py
for those).  Safe to re-run: huggingface_hub only fetches missing files.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

MODEL_REPO = "delta-lab-ai/lean-finder"
# `main` as of 2026-06-04 (the "newly optimized" model, not the paper's `old-model` branch).
MODEL_REVISION = "a9e312299c4d68f020662a68d70c4e3979781f91"
DEFAULT_MODELS_DIR = Path.home() / ".local/share/tauceti-finder/models"


def model_dir(models_dir: Path = DEFAULT_MODELS_DIR, revision: str = MODEL_REVISION) -> Path:
    return models_dir / f"lean-finder-{revision[:12]}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    ap.add_argument("--revision", default=MODEL_REVISION)
    args = ap.parse_args()

    target = model_dir(args.models_dir, args.revision)
    target.mkdir(parents=True, exist_ok=True)
    print(f"snapshot_download({MODEL_REPO!r}, revision={args.revision!r}) -> {target}", flush=True)
    path = snapshot_download(
        repo_id=MODEL_REPO,
        repo_type="model",
        revision=args.revision,
        local_dir=str(target),
        # The repo also hosts the public Mathlib corpora and FAISS indices (~11 GB); not model files.
        ignore_patterns=["*.index", "*.jsonl"],
    )
    (target / "PINNED_REVISION").write_text(f"{MODEL_REPO}\n{args.revision}\n")
    files = sorted(p.name for p in Path(path).iterdir() if p.is_file())
    total = sum(p.stat().st_size for p in Path(path).iterdir() if p.is_file())
    print(json.dumps({"path": path, "files": files, "bytes": total}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
