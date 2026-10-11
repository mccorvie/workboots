"""Download precomputed FAISS indices and corpus JSONLs from the HF model repo."""

from huggingface_hub import hf_hub_download

REPO_ID = "delta-lab-ai/lean-finder"
REPO_TYPE = "model"
REVISION = "main"
LOCAL_DIR = "data"

FILES = [
    # FAISS indices
    "mathlib4_19_0.index",
    "mathlib4_24_0.index",
    "mathlib4_28_0.index",
    # Corpus JSONLs
    "mathlib4_19_0.jsonl",
    "mathlib4_24_0.jsonl",
    "mathlib4_28_0.jsonl",
]

for fn in FILES:
    print(f"Downloading {fn} -> {LOCAL_DIR}/{fn} ...", flush=True)
    hf_hub_download(
        repo_id=REPO_ID,
        repo_type=REPO_TYPE,
        revision=REVISION,
        filename=fn,
        local_dir=LOCAL_DIR,
    )
    print(f"  Saved to {LOCAL_DIR}/{fn}", flush=True)

print("All downloads complete.")
