import os
import sys
import argparse
import json
import multiprocessing
import traceback
import time
import torch
import torch.nn.functional as F
import numpy as np
import faiss
from transformers import AutoTokenizer, AutoModel

LOG_INTERVAL = 50  # print progress every N batches


def log(device, msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}][{device}] {msg}", flush=True)


def last_token_pool(last_hidden_states, attention_mask):
    left_padding = (attention_mask[:, -1].sum() == attention_mask.shape[0])
    if left_padding:
        return last_hidden_states[:, -1]
    else:
        sequence_lengths = attention_mask.sum(dim=1) - 1
        batch_size = last_hidden_states.shape[0]
        return last_hidden_states[torch.arange(batch_size, device=last_hidden_states.device), sequence_lengths]


def _get(obj, field):
    return str(obj.get(field))


def format_entry(obj):
    # Match embed.py corpus format exactly — field names mapped from mathlib4 schema
    return (
        "{'formal_name': '" + _get(obj, "name") +
        "', 'kind': '" + _get(obj, "kind") +
        "', 'type': '" + _get(obj, "typeFull") +
        "', 'docstring': '" + _get(obj, "docString") + "'}"
    )


def build_one(input_path, output_path, device, model_path, batch_size):
    try:
        log(device, f"Loading tokenizer from {model_path} ...")
        tokenizer = AutoTokenizer.from_pretrained(model_path, padding_side='left', fix_mistral_regex=True)

        log(device, "Loading model ...")
        model = AutoModel.from_pretrained(model_path, torch_dtype=torch.bfloat16, trust_remote_code=True)
        model = model.eval().to(device)
        log(device, "Model ready.")

        log(device, f"Reading corpus from {input_path} ...")
        texts = []
        with open(input_path, 'r') as f:
            for line in f:
                obj = json.loads(line)
                texts.append(format_entry(obj))
        log(device, f"Loaded {len(texts)} entries.")

        total_batches = (len(texts) + batch_size - 1) // batch_size
        log(device, f"Embedding {total_batches} batches (batch_size={batch_size}) ...")

        all_embeddings = []
        t0 = time.time()
        for batch_idx, start in enumerate(range(0, len(texts), batch_size)):
            batch = texts[start:start + batch_size]
            inputs = tokenizer(batch, padding=True, truncation=True, max_length=8192, return_tensors="pt")
            inputs = {k: v.to(device) for k, v in inputs.items()}
            with torch.no_grad():
                outputs = model(**inputs)
            emb = last_token_pool(outputs.last_hidden_state, inputs["attention_mask"]).float() # cast pooled vector to fp32 before normalize
            emb = F.normalize(emb, p=2, dim=1)
            all_embeddings.append(emb.cpu().numpy())

            if (batch_idx + 1) % LOG_INTERVAL == 0 or batch_idx == total_batches - 1:
                elapsed = time.time() - t0
                rate = (batch_idx + 1) / elapsed
                eta = (total_batches - batch_idx - 1) / rate if rate > 0 else 0
                log(device, f"Batch {batch_idx+1}/{total_batches} | {rate:.1f} batch/s | ETA {eta/60:.1f} min")

        log(device, "Stacking embeddings ...")
        embeddings_matrix = np.vstack(all_embeddings).astype(np.float32)
        log(device, f"Embedding matrix: {embeddings_matrix.shape}")

        log(device, "Building FAISS index ...")
        index = faiss.IndexFlatIP(embeddings_matrix.shape[1])
        index.add(embeddings_matrix)
        faiss.write_index(index, output_path)
        log(device, f"Index saved to {output_path}. Done.")

    except Exception:
        log(device, "WORKER FAILED with exception:")
        traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
        raise


def main():
    parser = argparse.ArgumentParser(description="Build FAISS indices for Lean corpus files in parallel.")
    parser.add_argument('--model', type=str, default="delta-lab-ai/lean-finder", help="Model name")
    parser.add_argument('--inputs', nargs='+', required=True, help="Input JSONL corpus files")
    parser.add_argument('--outputs', nargs='+', required=True, help="Output FAISS index paths")
    parser.add_argument('--batch_size', type=int, default=32)
    parser.add_argument('--devices', nargs='+', default=['cuda:0', 'cuda:1', 'cuda:2'])
    args = parser.parse_args()

    if len(args.inputs) != len(args.outputs):
        parser.error("Number of --inputs and --outputs must match.")
    if len(args.inputs) > len(args.devices):
        parser.error(f"Need at least {len(args.inputs)} devices, got {len(args.devices)}.")

    print(f"Launching {len(args.inputs)} workers ...", flush=True)
    processes = []
    for inp, out, dev in zip(args.inputs, args.outputs, args.devices):
        p = multiprocessing.Process(
            target=build_one,
            args=(inp, out, dev, args.model, args.batch_size),
        )
        p.start()
        print(f"  Started worker PID {p.pid} -> {dev} | {inp} -> {out}", flush=True)
        processes.append(p)

    for p in processes:
        p.join()

    failed = [i for i, p in enumerate(processes) if p.exitcode != 0]
    if failed:
        print(f"WARNING: workers for {[args.inputs[i] for i in failed]} exited with errors (exit codes: {[processes[i].exitcode for i in failed]}).", flush=True)
    else:
        print("All indices built successfully.", flush=True)


if __name__ == "__main__":
    multiprocessing.set_start_method('spawn')
    main()
