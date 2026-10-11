#!/bin/bash
# Real b3 describe run / resume (same command both times). Key is read from the key file, never printed.
cd "$(dirname "$0")"
export HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES=
export OPENROUTER_API_KEY="$(cat ~/.config/tauceti/openrouter_key.txt)"
exec .venv/bin/python build_explore.py b3-11bc13335fb9 --stage describe \
  --reuse-from ~/.local/share/tauceti-explore/upstream/20260714_172516 --reuse-strict \
  --informalize-model anthropic/claude-haiku-5.5 --est-output-tokens 340 \
  --max-tokens 1000 --max-spend 65 --commit-every 10 --i-approve-spend \
  --informalize-max-concurrent 24
