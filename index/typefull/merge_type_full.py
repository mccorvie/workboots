#!/usr/bin/env python3
"""merge_type_full.py DECLS TYPEFULL MANIFEST [--meta k=v ...]
Rewrites DECLS in place adding `type_full` (from dump_typefull.lean's `typeFull`, joined by name; null if absent),
and updates MANIFEST (decls sha256/size, type_full coverage). Original is kept as DECLS.pre-typefull only if absent."""
import hashlib, json, os, sys, datetime
decls, tfp, man = sys.argv[1:4]
meta = dict(a.split("=", 1) for a in sys.argv[4:] if "=" in a)
tf = {}
for l in open(tfp, encoding="utf-8"):
    r = json.loads(l); tf[r["name"]] = r["typeFull"]
tmp = decls + ".tmp"; hit = miss = 0
by = {}
with open(decls, encoding="utf-8") as f, open(tmp, "w", encoding="utf-8") as o:
    for l in f:
        d = json.loads(l)
        t = tf.get(d["name"]); d["type_full"] = t
        if t is None: miss += 1
        else: hit += 1
        pk = "TauCeti" if d["module"].startswith("TauCeti.") else "Mathlib" if d["is_mathlib"] else "other"
        b = by.setdefault(pk, [0, 0]); b[0 if t is not None else 1] += 1
        o.write(json.dumps(d, ensure_ascii=False) + "\n")
os.replace(tmp, decls)
h = hashlib.sha256(open(decls, "rb").read()).hexdigest()
m = json.load(open(man))
m["decls_sha256"] = h; m["decls_size_bytes"] = os.path.getsize(decls)
m["type_full"] = {"with": hit, "without": miss, "by_group_with_without": by, "dump_rows": len(tf),
                  "width": 120, "method": "ppExpr of constant type via dump_typefull.lean (copied from local-finder/scripts)", **meta}
m["fields"] = ["name","kind","module","file","line","signature","docstring","is_mathlib","hash","type_full"]
json.dump(m, open(man, "w"), indent=2); open(man, "a").write("\n")
print(f"type_full: {hit} with, {miss} without; by group {by}")
