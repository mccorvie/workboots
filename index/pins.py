#!/usr/bin/env python3
"""Derive the per-batch TauCeti index pins from the bench corpus index and the TauCeti git mirror.

Rule (workboots-plan.md, decision 2): one frozen index per corpus batch, built at the oldest
merge base of the batch's `fix` captures, verified to be an ancestor of every other merge base in
the batch. Captures whose `lean_toolchain` is not the batch's majority toolchain are excluded
before the pin is derived (b2 has two stray v4.34.0-rc1 captures whose merge base `ee6673df`
predates the rc2 bump; the bench excludes them from selections as well).

Usage: pins.py [--admitted PATH] [--mirror PATH] [--json]
Prints one line per batch (or a JSON document with --json). Stdlib only; needs `git`.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys

DEFAULT_ADMITTED = os.path.expanduser("~/tauceti-bench-data/index/records-latest/admitted.jsonl")
DEFAULT_MIRROR = "/srv/tauceti/records/git/TauCeti.git"

# worker_sha prefixes that identify a corpus batch (TauCetiWorker commit the captures ran on).
BATCHES = {
    "b2": ("5b7d1e0c87c2",),
    "b3": ("399e02106c3e", "3959d58495dd"),
}


def git(mirror: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", mirror, *args], capture_output=True, text=True)


def batch_of(row: dict) -> str | None:
    worker = row.get("worker_sha", "")
    for batch, prefixes in BATCHES.items():
        if worker.startswith(prefixes):
            return batch
    return None


def derive(admitted: str, mirror: str) -> dict:
    rows = [json.loads(line) for line in open(admitted, encoding="utf-8") if line.strip()]
    fix = [r for r in rows if r.get("phase") == "fix"]
    result = {}
    for batch in BATCHES:
        sel = [r for r in fix if batch_of(r) == batch]
        toolchains = collections.Counter(r["lean_toolchain"] for r in sel)
        majority, _ = toolchains.most_common(1)[0]
        excluded = [r for r in sel if r["lean_toolchain"] != majority]
        kept = [r for r in sel if r["lean_toolchain"] == majority]
        merge_bases = sorted({r["merge_base_sha"] for r in kept})
        dated = []
        for sha in merge_bases:
            out = git(mirror, "log", "-1", "--format=%ct %cI", sha)
            if out.returncode != 0 or not out.stdout.strip():
                raise SystemExit(f"{batch}: merge base {sha} is not in {mirror}")
            ts, _, iso = out.stdout.strip().partition(" ")
            dated.append((int(ts), sha, iso))
        dated.sort()
        _, pin, pin_date = dated[0]
        not_ancestor = [
            sha for _, sha, _ in dated[1:]
            if git(mirror, "merge-base", "--is-ancestor", pin, sha).returncode != 0
        ]
        toolchain = git(mirror, "show", f"{pin}:lean-toolchain").stdout.strip()
        manifest = json.loads(git(mirror, "show", f"{pin}:lake-manifest.json").stdout)
        mathlib_rev = next(p["rev"] for p in manifest["packages"] if p["name"] == "mathlib")
        result[batch] = {
            "batch": batch,
            "pin": f"{batch}-{pin[:12]}",
            "tauceti_sha": pin,
            "pin_committed_at": pin_date,
            "lean_toolchain": toolchain,
            "mathlib_rev": mathlib_rev,
            "captures": len(kept),
            "merge_bases": len(dated),
            "pin_is_ancestor_of_all_merge_bases": not not_ancestor,
            "merge_bases_not_descending_from_pin": not_ancestor,
            "excluded_captures": len(excluded),
            "excluded_toolchains": sorted({r["lean_toolchain"] for r in excluded}),
            "excluded_capture_ids": sorted(r["capture_id"] for r in excluded),
        }
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--admitted", default=DEFAULT_ADMITTED)
    ap.add_argument("--mirror", default=DEFAULT_MIRROR)
    ap.add_argument("--json", action="store_true", help="print the full derivation as JSON")
    args = ap.parse_args()
    pins = derive(args.admitted, args.mirror)
    if args.json:
        json.dump(pins, sys.stdout, indent=2)
        print()
        return 0
    for p in pins.values():
        flag = "" if p["pin_is_ancestor_of_all_merge_bases"] else "  !! NOT an ancestor of every merge base"
        print(
            f"{p['pin']}  tauceti={p['tauceti_sha']}  {p['pin_committed_at']}  {p['lean_toolchain']}  "
            f"mathlib={p['mathlib_rev'][:12]}  captures={p['captures']}  merge_bases={p['merge_bases']}  "
            f"excluded={p['excluded_captures']}{flag}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
