#!/bin/bash
# b2 residual describe (strict reuse from b3 then July upstream). Key read from the key file, never printed.
cd "$(dirname "$0")"
export HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES=
export OPENROUTER_API_KEY="$(cat ~/.config/tauceti/openrouter_key.txt)"
S=~/.local/share/tauceti-explore
exec .venv/bin/python build_explore.py b2-b5500936b563 --stage describe \
  --reuse-from $S/b3-11bc13335fb9 --reuse-from $S/upstream/20260714_172516 --reuse-strict \
  --informalize-model anthropic/claude-haiku-5.5 --est-output-tokens 340 \
  --max-tokens 1000 --max-spend 10 --commit-every 10 --i-approve-spend \
  --informalize-max-concurrent 24
