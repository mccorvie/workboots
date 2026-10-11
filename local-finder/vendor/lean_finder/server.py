"""
Persistent lean-finder retrieval server.

Loads the model and FAISS indices once at startup, then serves search
requests over HTTP so other processes don't pay the loading cost each time.

Usage:
    python server.py

Endpoints:
    POST /search   {"inputs": str, "top_k": int, "version": str}
    GET  /health
    GET  /versions
"""

import argparse
import json
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List

import faiss
import numpy as np
import torch
import torch.nn.functional as F
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from transformers import AutoModel, AutoTokenizer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_VERSION = "v4.28.0"

VERSION_MAP = {
    "v4.19.0": ("mathlib4_19_0.index", "mathlib4_19_0.jsonl"),
    "v4.24.0": ("mathlib4_24_0.index", "mathlib4_24_0.jsonl"),
    "v4.28.0": ("mathlib4_28_0.index", "mathlib4_28_0.jsonl"),
}

# ---------------------------------------------------------------------------
# Model + retrieval logic
# ---------------------------------------------------------------------------

def last_token_pool(last_hidden_states, attention_mask):
    left_padding = (attention_mask[:, -1].sum() == attention_mask.shape[0])
    if left_padding:
        return last_hidden_states[:, -1]
    else:
        sequence_lengths = attention_mask.sum(dim=1) - 1
        batch_size = last_hidden_states.shape[0]
        return last_hidden_states[
            torch.arange(batch_size, device=last_hidden_states.device),
            sequence_lengths,
        ]


def get_detailed_instruct(query: str) -> str:
    return f"You are an expert in Lean, now please find a lean statement corresponding to this description: {query}"


class EndpointHandler:
    def __init__(self, path: str = "."):
        data_dir = os.environ.get("DATA_DIR", path)
        self._init_model(path)
        self.indices: Dict[str, Any] = {}
        self.corpus_data: Dict[str, list] = {}
        for version, (index_file, jsonl_file) in VERSION_MAP.items():
            index_path = os.path.join(data_dir, index_file)
            jsonl_path = os.path.join(data_dir, jsonl_file)
            self.indices[version] = faiss.read_index(index_path)
            self.corpus_data[version] = self._load_jsonl(jsonl_path)
            print(f"Loaded {version}: {len(self.corpus_data[version])} entries")

    def _load_jsonl(self, path: str) -> list:
        data = []
        with open(path) as f:
            for line in f:
                data.append(json.loads(line))
        return data

    def _init_model(self, path: str):
        self.tokenizer = AutoTokenizer.from_pretrained(
            path, padding_side="left", fix_mistral_regex=True
        )
        model = AutoModel.from_pretrained(path, torch_dtype=torch.bfloat16, trust_remote_code=True)
        if model.config.pad_token_id is None:
            model.config.pad_token_id = 0
        self.model = model.eval().to("cuda")
        print(f"Model loaded from {path}")

    def _get_embeddings(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        inputs = self.tokenizer(
            texts, padding=True, truncation=True, max_length=8192, return_tensors="pt"
        )
        inputs = {k: v.to("cuda") for k, v in inputs.items()}
        with torch.no_grad():
            outputs = self.model(**inputs)
        emb = last_token_pool(outputs.last_hidden_state, inputs["attention_mask"]).float()
        emb = F.normalize(emb, p=2, dim=1)
        return emb

    def __call__(self, data: Dict[str, Any]) -> Dict[str, Any]:
        query = data.get("inputs", "").strip()
        if not query:
            return {"error": "Missing 'inputs' field in request."}

        top_k = data.get("top_k", 10)
        version = data.get("version", DEFAULT_VERSION)
        if version not in VERSION_MAP:
            return {"error": f"Unknown version '{version}'. Valid options: {list(VERSION_MAP.keys())}"}

        print(f"query: {query!r}, top_k: {top_k}, version: {version}")

        instructed = get_detailed_instruct(query)
        query_emb = self._get_embeddings(instructed).cpu().detach().float().numpy()

        all_scores, all_indices = self.indices[version].search(query_emb, top_k)

        corpus = self.corpus_data[version]
        results = []
        for docid, score in zip(all_indices[0], all_scores[0]):
            entry = corpus[docid]
            path_dotted = entry.get("path", "").replace("/", ".")
            results.append({
                "score": float(score),
                "formal_name": entry.get("name", ""),
                "informal_name": entry.get("informal_name", ""),
                "kind": entry.get("kind", ""),
                "type": entry.get("typeFull", ""),
                "informal_description": entry.get("informal_description", ""),
                "path": path_dotted,
            })

        return {"results": results}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    inputs: str
    top_k: int = 10
    version: str = DEFAULT_VERSION


class SearchResult(BaseModel):
    score: float
    formal_name: str
    informal_name: str
    kind: str
    type: str
    informal_description: str
    path: str


class SearchResponse(BaseModel):
    results: List[SearchResult]


# ---------------------------------------------------------------------------
# App lifecycle — handler loaded once, kept alive for all requests
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    args = app.state.args
    data_dir = os.path.abspath(args.data_dir)
    os.environ["DATA_DIR"] = data_dir
    print(f"Loading handler: model_dir={args.model_dir!r}  data_dir={data_dir!r}")
    app.state.handler = EndpointHandler(args.model_dir)
    print("Handler ready — accepting requests")
    yield


app = FastAPI(title="lean-finder", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/versions")
def versions():
    return {"versions": list(VERSION_MAP.keys()), "default": DEFAULT_VERSION}


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest, request: Request):
    result = request.app.state.handler({
        "inputs": req.inputs,
        "top_k": req.top_k,
        "version": req.version,
    })
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return SearchResponse(results=result["results"])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="lean-finder retrieval server")
    parser.add_argument(
        "--model-dir",
        default="delta-lab-ai/lean-finder",
        help="Local path or HuggingFace model ID for the embedding model",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory containing .index and .jsonl files (relative to cwd, default: data/)",
    )
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    app.state.args = args
    uvicorn.run(app, host="0.0.0.0", port=args.port)
