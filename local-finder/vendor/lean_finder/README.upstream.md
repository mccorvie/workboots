<div align="center">
  <img src="https://huggingface.co/spaces/delta-lab-ai/Lean-Finder/resolve/main/lean_finder_logo.png" alt="Lean Finder Logo" width="200"/>
  <h1>Lean Finder: Semantic Search For Mathlib That Understands User Intents</h1>
</div>

<div align="center">

[![Website](https://img.shields.io/badge/Homepage-LeanFinder-536af5?color=536af5&logoColor=white)](https://leanfinder.github.io/)
[![Model](https://img.shields.io/badge/HuggingFace-Model-yellow?logo=huggingface)](https://huggingface.co/delta-lab-ai/lean-finder)
[![arXiv](https://img.shields.io/badge/arXiv-2510.15940-b31b1b.svg?style=flat)](https://arxiv.org/abs/2510.15940)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

</div>

**Lean Finder** is a semantic search engine for Lean and mathlib that understands and aligns with the intents of mathematicians. The model can be accessed at [**delta-lab-ai/lean-finder**](https://huggingface.co/delta-lab-ai/lean-finder).

This repository provides the code to reproduce the results in the paper and **self-host** the retrieval service: download the precomputed FAISS indices, load the model, and serve queries over HTTP. **Multiple mathlib versions are supported out of the box** (currently `v4.19.0`, `v4.24.0`, and `v4.28.0`). 

---

## Quick start: use the web service

The easiest way to use Lean Finder is through our hosted web service:

👉 **[leanfinder.github.io](https://leanfinder.github.io/)**

No setup required. Use this if your workload is light or interactive.

---

## Self-host the retrieval service

If you have a heavy workload (batch retrieval, integration with a prover, large-scale evaluation), host the model yourself with the code in this repository.

### 1. Install

```bash
git clone https://github.com/delta-lab-ai/lean-finder.git
cd lean-finder
pip install -r requirements.txt
```

**Hardware.** A single GPU with at least 16GB of VRAM is required to run the model.

### 2. Download the corpus and indices

For each supported mathlib version, the server needs two files: a **JSONL corpus** (the actual declarations) and a **FAISS index** (precomputed embeddings of that corpus). Both are hosted on the [Hugging Face model repo](https://huggingface.co/delta-lab-ai/lean-finder) and are downloaded with a single command:

```bash
python download_corpus.py
```

This populates `data/` with the corpus files (`mathlib4_19_0.jsonl`, `mathlib4_24_0.jsonl`, `mathlib4_28_0.jsonl`) and the matching FAISS indices (`mathlib4_19_0.index`, `mathlib4_24_0.index`, `mathlib4_28_0.index`). **This step is required** — the corpus JSONLs are too large to ship in the git repo, so you must fetch them from Hugging Face before starting the server.

#### (Optional) Build an index over your own corpus

If you want to search over a different corpus — your own project, a different mathlib snapshot, or a private library — encode it yourself with [`build_index.py`](build_index.py). Your input JSONL must follow the same schema as our mathlib corpora (one JSON object per line with `path`, `kind`, `name`, `typeFull`, `informal_name`, `informal_description`, `docString` fields — inspect any of the downloaded `data/mathlib4_*.jsonl` files for examples).

```bash
python build_index.py \
  --inputs path/to/your_corpus.jsonl \
  --outputs path/to/your_corpus.index \
  --devices cuda:0 \
  --batch_size 32
```

You can pass multiple `--inputs`/`--outputs` pairs together with one `--devices` entry per input to embed several corpora in parallel.

### 3. Start the server

```bash
python server.py
```

On first launch the server downloads the model from [`delta-lab-ai/lean-finder`](https://huggingface.co/delta-lab-ai/lean-finder), loads it onto GPU, and loads every FAISS index into memory. Once you see `Handler ready — accepting requests`, the service is up at `http://localhost:8000`.

Useful flags:

```bash
python server.py --model-dir delta-lab-ai/lean-finder --data-dir data --port 8000
```

### 4. Query the server

A minimal client is provided in [`client_example.py`](client_example.py):

```bash
python client_example.py
```

Or use it as a library:

```python
from client_example import search

results = search(
    "Terminal Objects are Zero Objects",
    top_k=5,
    version="v4.28.0",
)
for r in results:
    print(r["score"], r["formal_name"], r["path"])
```

The HTTP API is small and easy to call from any language:

| Method | Endpoint   | Body                                                          |
|--------|------------|---------------------------------------------------------------|
| POST   | `/search`  | `{"inputs": <query>, "top_k": <int>, "version": <version>}`   |
| GET    | `/health`  | —                                                             |
| GET    | `/versions`| —                                                             |

Each search result includes `score`, `formal_name`, `informal_name`, `kind`, `type`, `informal_description`, and `path`.

---

## Supported mathlib versions

Lean Finder ships indices for multiple mathlib versions so you can search against the snapshot that matches your project:

| Version    | Index file               | Corpus file              |
|------------|--------------------------|--------------------------|
| `v4.19.0`  | `mathlib4_19_0.index`    | `mathlib4_19_0.jsonl`    |
| `v4.24.0`  | `mathlib4_24_0.index`    | `mathlib4_24_0.jsonl`    |
| `v4.28.0`  | `mathlib4_28_0.index`    | `mathlib4_28_0.jsonl`    |

Pass `version` in the request body to choose. Default is `v4.28.0`.

---

## A note on model versions

The model loaded by [`server.py`](server.py) (`delta-lab-ai/lean-finder`, `main` revision) is a **newly updated and optimized** version that is **significantly stronger** than the model described in the original paper. On our internal evaluations it shows substantial improvements in retrieval quality. We recommend this version for all practical use.

Full evaluation details for the updated model will be released soon.

If you want to **reproduce the results in the paper**, use the original model and code in the [`lean-finder-original`](lean-finder-original/README.md) folder — see its README for instructions. For any other use, we do not recommend the original model.

---

## Citation

```bibtex
@article{lu2025lean,
  title={Lean finder: Semantic search for mathlib that understands user intents},
  author={Lu, Jialin and Emond, Kye and Yang, Kaiyu and Chaudhuri, Swarat and Sun, Weiran and Chen, Wuyang},
  journal={arXiv preprint arXiv:2510.15940},
  year={2025}
}
```

## License

Apache 2.0.

## Acknowledgements

This project builds on the following open-source projects and datasets for Lean:

- [**jixia**](https://github.com/frenzymath/jixia)
- [**LeanDojo**](https://github.com/lean-dojo/leandojo)
- [**Herald**](https://huggingface.co/datasets/FrenzyMath/mathlib_informal_v4.19.0)

We thank the authors and maintainers of these projects.
