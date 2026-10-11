#!/usr/bin/env python3
"""Compact view of mcp_probe.py output: one line per step with timing, status and the fields that matter."""
import json, sys

def pick(d):
    s = d.get("structured") or {}
    out = {}
    for k in ("version", "success", "readiness", "document_progress"):
        if k in s:
            v = s[k]
            out[k] = v if not isinstance(v, dict) else {kk: v[kk] for kk in v if kk in ("save_ready", "reason", "done", "updates", "blocking_error_count")}
    if "diagnostics" in s and isinstance(s["diagnostics"], dict):
        out["diag"] = s["diagnostics"].get("counts")
        if s["diagnostics"].get("items"):
            out["items"] = [i.get("message", "")[:80] for i in s["diagnostics"]["items"]][:3]
    if "messages" in s:
        out["messages"] = [(m.get("severity"), (m.get("message") or m.get("text") or "")[:100]) for m in s["messages"]][:3]
    if "proof_state" in s and s["proof_state"]:
        ps = s["proof_state"]
        out["proof_state"] = (json.dumps(ps)[:160])
    if "olean" in s:
        out["olean"] = s["olean"].rsplit("/", 1)[-1]
    if "items" in s and isinstance(s["items"], list) and s["items"] and isinstance(s["items"][0], dict) and "snippet" in s["items"][0]:
        out["attempts"] = [(i["snippet"][:30], len(i.get("goals", [])), [(m.get("severity"), m.get("message", "")[:60]) for m in i.get("diagnostics", [])][:2]) for i in s["items"]]
    if d.get("error"):
        e = d["error"]
        out["error"] = {k: e.get(k) for k in ("code", "message")} if isinstance(e, dict) else e
        if isinstance(e, dict) and isinstance(e.get("data"), dict):
            out["error"]["data"] = {k: e["data"][k] for k in e["data"] if k in ("code", "reason", "kind", "recovery")}
    if d.get("isError") and d.get("text"):
        out["error_text"] = d["text"][:300]
    if "text" in d and not out and not d.get("isError"):
        out["text"] = d["text"][:200].replace("\n", " | ")
    return out

for path in sys.argv[1:]:
    print(f"### {path}")
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if "note" in d:
            print(f"  -- {d['note']}"); continue
        if "shell" in d:
            print(f"  $ {d['shell'][:80]} -> rc={d['rc']} {d['out'][:120]!r}"); continue
        if "names" in d:
            print(f"  {d['id']:<28} {d['ms']:>7} ms  tools={d['count']}  {d['names']}"); continue
        if "server_exit" in d:
            print(f"  server_exit={d['server_exit']} notifications={d['notifications']} peak_rss_kb={d['peak_rss_kb']} total_ms={d['total_ms']}"); continue
        if "par_wall_ms" in d:
            print(f"  [par wall {d['par_wall_ms']} ms for {d['n']} calls]"); continue
        status = "ok " if d.get("ok") else "ERR"
        print(f"  {status} {str(d.get('id')):<28} {d.get('ms', 0):>7} ms  {json.dumps(pick(d), ensure_ascii=False)[:600]}")
