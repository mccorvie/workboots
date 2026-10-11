#!/usr/bin/env python3
"""Select the describer-pilot sample: 200 TauCeti declarations, stratified.

    select_pilot.py ~/.local/share/tauceti-index/<pin>/decls.jsonl --out-dir pilot/ [--n 200]

Strata (two axes):
  kind group : theorem (theorem+lemma) | def (def+abbrev) | structure (structure+class+inductive)
               | instance | other
  vocabulary : bespoke  -> the signature or docstring mentions a TauCeti-defined name
               mathlib  -> it only uses Mathlib/core vocabulary
Allocation is proportional to the population of each (vocab, kind) cell with a floor of
--min-per-cell (when the cell has that many), deterministic under --seed. Auto-generated
names (``.mk``, ``.match_``, ``.proof_``, ``_eq_``, ``.eq_1`` ...) and declarations without a
signature are excluded, as LeanExplore itself filters ``.mk`` constructors.

Outputs (all under --out-dir):
  pilot-<n>.jsonl        the selected rows (full decls.jsonl records + cell labels)
  pilot-names.txt        one name per line (feed to build_explore.py --names)
  pilot-cells.md         the allocation table
  rank-queries.csv       30 names (round robin over cells) with an empty `query` column to
                         fill in by hand, then columns for the rank each model achieves
Run with ``python3 -I``; stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

KIND_GROUP = {"theorem": "theorem", "lemma": "theorem", "def": "def", "abbrev": "def",
              "structure": "structure", "class": "structure", "inductive": "structure", "instance": "instance"}
AUTOGEN_RE = re.compile(r"(\.mk$|\.match_\d|\.proof_\d|\.eq_\d+$|\._|\.inj$|\.injEq$|\.sizeOf_spec$|\.noConfusion|\.rec$|\.recOn$|\.casesOn$|\.below$|\.brecOn$|\.ibelow$|\.binductionOn$|_eq_\d+$|\.inst[A-Z]\w*$)")
IDENT_RE = re.compile(r"[A-Za-z_Ͱ-Ͽ][\w'!?Ͱ-Ͽ₀-ₜ]*(?:\.[A-Za-z_Ͱ-Ͽ][\w'!?Ͱ-Ͽ₀-ₜ]*)*")
GENERIC = {"TauCeti"}


def load(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def is_tc(r: dict) -> bool:
    """TauCeti-owned: module under TauCeti.* (is_mathlib=false also covers Lean core/Batteries)."""
    return (r.get("module") or "").split(".")[0] == "TauCeti"


def bespoke_vocab(rows: list[dict]) -> set[str]:
    """Names (and their namespace-stripped tails, when unambiguous and >= 6 chars) defined by TauCeti."""
    tc = [r["name"] for r in rows if is_tc(r)]
    foreign = {r["name"] for r in rows if not is_tc(r)}
    full = set(tc)
    tails: dict[str, set[str]] = defaultdict(set)
    for n in tc:
        parts = n.split(".")
        for i in range(1, len(parts)):
            t = ".".join(parts[i:])
            tails[t].add(n)
    vocab = set(full)
    tails = {t: o for t, o in tails.items() if t not in foreign}  # a name Mathlib/core also defines is not bespoke
    for t, owners in tails.items():
        if len(owners) == 1 and len(t) >= 6 and t not in GENERIC and not t[0].islower():
            vocab.add(t)
        elif len(owners) == 1 and len(t) >= 10:
            vocab.add(t)
    return vocab


def is_bespoke(r: dict, vocab: set[str]) -> bool:
    text = f"{r.get('signature', '')}\n{r.get('docstring') or ''}"
    own = r["name"]
    for tok in IDENT_RE.findall(text):
        if tok == own or own.endswith("." + tok):
            continue
        if tok in vocab:
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("decls", type=Path)
    ap.add_argument("--out-dir", type=Path, default=Path("pilot"))
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--n-queries", type=int, default=30)
    ap.add_argument("--min-per-cell", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20261008)
    ap.add_argument("--require-docstring-share", type=float, default=0.0,
                    help="optional: force at least this share of the sample to have docstrings (0 = natural rate)")
    a = ap.parse_args()

    rows = load(a.decls)
    tc = [r for r in rows if is_tc(r) and r.get("kind") != "ctor" and r.get("signature") and not AUTOGEN_RE.search(r["name"])]
    vocab = bespoke_vocab(rows)
    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in tc:
        kg = KIND_GROUP.get(r.get("kind", ""), "other")
        vb = "bespoke" if is_bespoke(r, vocab) else "mathlib"
        r["_cell_kind"], r["_cell_vocab"] = kg, vb
        cells[(vb, kg)].append(r)

    pop = {c: len(v) for c, v in cells.items()}
    total = sum(pop.values())
    rnd = random.Random(a.seed)
    # proportional allocation with floors, largest-remainder rounding
    alloc = {c: min(pop[c], a.min_per_cell) for c in pop}
    remaining = a.n - sum(alloc.values())
    if remaining > 0:
        shares = {c: pop[c] / total * remaining for c in pop}
        for c in pop:
            alloc[c] += int(shares[c])
        rem = a.n - sum(alloc.values())
        for c, _ in sorted(shares.items(), key=lambda kv: kv[1] - int(kv[1]), reverse=True):
            if rem <= 0:
                break
            if alloc[c] < pop[c]:
                alloc[c] += 1; rem -= 1
    for c in pop:
        alloc[c] = min(alloc[c], pop[c])

    selected: list[dict] = []
    for c in sorted(cells):
        pool = sorted(cells[c], key=lambda r: r["name"])
        if a.require_docstring_share:
            pool.sort(key=lambda r: (not r.get("docstring"), r["name"]))
            k = alloc[c]
            with_doc = [r for r in pool if r.get("docstring")]
            want_doc = min(len(with_doc), int(round(k * a.require_docstring_share)))
            pick = rnd.sample(with_doc, want_doc) + rnd.sample([r for r in pool if not r.get("docstring")] or with_doc, k - want_doc)
        else:
            pick = rnd.sample(pool, alloc[c])
        selected.extend(pick)

    a.out_dir.mkdir(parents=True, exist_ok=True)
    out_jsonl = a.out_dir / f"pilot-{len(selected)}.jsonl"
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for r in selected:
            d = {k: v for k, v in r.items() if not k.startswith("_")}
            d["cell_kind"], d["cell_vocab"] = r["_cell_kind"], r["_cell_vocab"]
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    (a.out_dir / "pilot-names.txt").write_text("\n".join(r["name"] for r in selected) + "\n")

    lines = [f"# Pilot allocation ({len(selected)} of {total} eligible TauCeti declarations; seed {a.seed})", "",
             "| vocab | kind | population | selected | with docstring (selected) |", "|---|---|---:|---:|---:|"]
    for c in sorted(pop):
        sel = [r for r in selected if (r["_cell_vocab"], r["_cell_kind"]) == c]
        lines.append(f"| {c[0]} | {c[1]} | {pop[c]} | {len(sel)} | {sum(1 for r in sel if r.get('docstring'))} |")
    lines += ["", f"Total TauCeti rows in extraction: {sum(1 for r in rows if is_tc(r))}; excluded as auto-generated/no signature: {sum(1 for r in rows if is_tc(r)) - total}",
              f"Bespoke-vocabulary size: {len(vocab)} names/tails", f"Kinds in extraction (TauCeti): {dict(Counter(r.get('kind') for r in rows if is_tc(r)))}"]
    (a.out_dir / "pilot-cells.md").write_text("\n".join(lines) + "\n")

    # rank-test queries: round robin over cells
    by_cell: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in selected:
        by_cell[(r["_cell_vocab"], r["_cell_kind"])].append(r)
    for v in by_cell.values():
        rnd.shuffle(v)
    picks: list[dict] = []
    while len(picks) < min(a.n_queries, len(selected)):
        progressed = False
        for c in sorted(by_cell):
            if by_cell[c] and len(picks) < a.n_queries:
                picks.append(by_cell[c].pop()); progressed = True
        if not progressed:
            break
    with (a.out_dir / "rank-queries.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "cell_vocab", "cell_kind", "signature", "query (write a natural-language query a prover would type; do not paste the name)",
                    "rank_model_A", "rank_model_B", "rank_model_C", "rank_no_description", "notes"])
        for r in picks:
            w.writerow([r["name"], r["_cell_vocab"], r["_cell_kind"], " ".join(r["signature"].split()), "", "", "", "", "", ""])
    print("\n".join(lines))
    print(f"\nwrote {out_jsonl}, pilot-names.txt, pilot-cells.md, rank-queries.csv ({len(picks)} queries) in {a.out_dir}")


if __name__ == "__main__":
    main()
