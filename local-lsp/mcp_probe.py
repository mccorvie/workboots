#!/usr/bin/env python3
"""Minimal stdio MCP JSON-RPC client for smoke-testing Lean MCP servers without an LLM.

Usage:
  mcp_probe.py [--env K=V ...] [--cwd DIR] [--timeout S] [--log FILE] -- CMD [ARGS...]  < script.jsonl

Reads a script of JSON lines from stdin. Each line is an object:
  {"op": "list"}                                       -> tools/list, prints names
  {"op": "call", "name": T, "args": {...}, "id": "x"}  -> tools/call, waits for the response
  {"op": "par", "calls": [{"name":..,"args":..,"id":..}, ...]}  -> send all, wait for all
  {"op": "sleep", "s": 2}
  {"op": "shell", "cmd": "..."}                        -> run a shell command (e.g. edit a file)
  {"op": "note", "text": "..."}
Every response is printed as one JSON line on stdout:
  {"id":..,"name":..,"ms":..,"ok":bool,"result":..|"error":..}
Notifications from the server (progress, logging) are counted and summarised; server stderr goes
to --log (default: stderr passthrough). Exit status 0 even when a call returns an error; nonzero
only if the transport breaks.
"""
import argparse
import json
import re
import os
import queue
import subprocess
import sys
import threading
import time

PROTOCOL = "2025-11-25"  # Beam's legacy family; the python mcp SDK negotiates down from this fine


class Client:
    def __init__(self, cmd, env, cwd, log, timeout):
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, cwd=cwd, env=env, bufsize=0
        )
        self.timeout = timeout
        self.next_id = 1
        self.pending = {}
        self.lock = threading.Lock()
        self.notifications = []
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()

    def _read_loop(self):
        buf = b""
        f = self.proc.stdout
        while True:
            line = f.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self.notifications.append({"raw": line[:200].decode("utf-8", "replace")})
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                with self.lock:
                    q = self.pending.get(msg["id"])
                if q is not None:
                    q.put(msg)
                else:
                    self.notifications.append({"orphan": msg})
            else:
                self.notifications.append(msg)

    def send(self, obj):
        data = (json.dumps(obj) + "\n").encode()
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def request_async(self, method, params=None):
        with self.lock:
            rid = self.next_id
            self.next_id += 1
            q = queue.Queue()
            self.pending[rid] = q
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)
        return rid, q

    def wait(self, rid, q, timeout=None):
        try:
            return q.get(timeout=timeout or self.timeout)
        except queue.Empty:
            return {"id": rid, "error": {"code": -1, "message": f"client timeout after {timeout or self.timeout}s"}}
        finally:
            with self.lock:
                self.pending.pop(rid, None)

    def request(self, method, params=None, timeout=None):
        rid, q = self.request_async(method, params)
        return self.wait(rid, q, timeout)

    def initialize(self, name="mcp_probe"):
        res = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": name, "version": "0.1"},
            },
        )
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return res

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


VERSIONS = {}
_PLACEHOLDER = re.compile(r"^@V([+-]\d+)?:(.+)$")


def resolve_placeholders(obj):
    """Replace "@V:<path>" / "@V+100:<path>" strings with the latest document version seen for <path>."""
    if isinstance(obj, dict):
        return {k: resolve_placeholders(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_placeholders(v) for v in obj]
    if isinstance(obj, str):
        m = _PLACEHOLDER.match(obj)
        if m:
            base = VERSIONS.get(m.group(2))
            if base is None:
                return obj  # leave it; the server will reject it visibly
            return base + int(m.group(1) or 0)
    return obj


def record_version(res):
    sc = res.get("result", {}).get("structuredContent") if "result" in res else None
    if isinstance(sc, dict) and isinstance(sc.get("path"), str) and isinstance(sc.get("version"), int):
        VERSIONS[sc["path"]] = sc["version"]
        if sc["path"].startswith("/"):
            VERSIONS[sc["path"].rsplit("/", 1)[-1]] = sc["version"]


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def summarize_result(res):
    """Keep the output readable: full structuredContent, truncated text content."""
    if "error" in res:
        return {"ok": False, "error": res["error"]}
    r = res.get("result", {})
    out = {"ok": not r.get("isError", False)}
    if r.get("isError"):
        out["isError"] = True
    if "structuredContent" in r:
        out["structured"] = r["structuredContent"]
    texts = [c.get("text", "") for c in r.get("content", []) if c.get("type") == "text"]
    if texts:
        joined = "\n".join(texts)
        out["text"] = joined if len(joined) <= 4000 else joined[:4000] + f"... [{len(joined)} chars]"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--cwd")
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--log", help="server stderr file (default: inherit)")
    ap.add_argument("--rss", help="write peak RSS (kB) of the server process tree to this file at exit")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        ap.error("missing server command")
    env = dict(os.environ)
    for kv in a.env:
        k, _, v = kv.partition("=")
        env[k] = v
    log = open(a.log, "ab") if a.log else None
    t0 = time.monotonic()
    c = Client(cmd, env, a.cwd, log, a.timeout)
    init = c.initialize()
    emit({"id": "initialize", "ms": int((time.monotonic() - t0) * 1000), **summarize_result(init), "pid": c.proc.pid})
    peak = {"rss_kb": 0}
    stop = threading.Event()

    def rss_sampler():
        while not stop.is_set():
            total = 0
            try:
                for pid in os.listdir("/proc"):
                    if not pid.isdigit():
                        continue
                    try:
                        with open(f"/proc/{pid}/status") as f:
                            st = f.read()
                    except OSError:
                        continue
                    if f"\nPPid:\t{c.proc.pid}\n" in st or int(pid) == c.proc.pid or _descends(pid, c.proc.pid):
                        for ln in st.splitlines():
                            if ln.startswith("VmRSS:"):
                                total += int(ln.split()[1])
                                break
            except Exception:
                pass
            peak["rss_kb"] = max(peak["rss_kb"], total)
            stop.wait(0.5)

    threading.Thread(target=rss_sampler, daemon=True).start()
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        step = json.loads(raw)
        op = step.get("op")
        if op == "note":
            emit({"note": step.get("text")})
        elif op == "sleep":
            time.sleep(step.get("s", 1))
        elif op == "shell":
            r = subprocess.run(step["cmd"], shell=True, capture_output=True, text=True)
            emit({"shell": step["cmd"], "rc": r.returncode, "out": (r.stdout + r.stderr)[-1000:]})
        elif op == "list":
            t = time.monotonic()
            res = c.request("tools/list", {})
            tools = res.get("result", {}).get("tools", [])
            emit({"id": step.get("id", "tools/list"), "ms": int((time.monotonic() - t) * 1000), "ok": "error" not in res,
                  "count": len(tools), "names": [x["name"] for x in tools], "error": res.get("error")})
            if step.get("schemas"):
                emit({"schemas": {x["name"]: x.get("inputSchema") for x in tools}})
        elif op == "call":
            t = time.monotonic()
            res = c.request("tools/call", {"name": step["name"], "arguments": resolve_placeholders(step.get("args", {}))}, step.get("timeout"))
            record_version(res)
            emit({"id": step.get("id", step["name"]), "name": step["name"], "ms": int((time.monotonic() - t) * 1000), **summarize_result(res)})
        elif op == "par":
            t = time.monotonic()
            started = []
            for cl in step["calls"]:
                rid, q = c.request_async("tools/call", {"name": cl["name"], "arguments": resolve_placeholders(cl.get("args", {}))})
                started.append((cl, rid, q, time.monotonic()))
            for cl, rid, q, ts in started:
                res = c.wait(rid, q)
                record_version(res)
                emit({"id": cl.get("id", cl["name"]), "name": cl["name"], "ms": int((time.monotonic() - ts) * 1000),
                      "par_total_ms": int((time.monotonic() - t) * 1000), **summarize_result(res)})
            emit({"par_wall_ms": int((time.monotonic() - t) * 1000), "n": len(started)})
        else:
            emit({"error": f"unknown op {op!r}"})
    stop.set()
    c.close()
    kinds = {}
    for n in c.notifications:
        k = n.get("method") or ("raw" if "raw" in n else "orphan")
        kinds[k] = kinds.get(k, 0) + 1
    emit({"server_exit": c.proc.returncode, "notifications": kinds, "peak_rss_kb": peak["rss_kb"], "total_ms": int((time.monotonic() - t0) * 1000)})
    if a.rss:
        with open(a.rss, "w") as f:
            f.write(str(peak["rss_kb"]) + "\n")
    if log:
        log.close()


def _descends(pid, root):
    """Is /proc/<pid> a (grand)child of root?"""
    seen = 0
    try:
        while seen < 32:
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            ppid = int(fields[1])
            if ppid == root:
                return True
            if ppid <= 1:
                return False
            pid = ppid
            seen += 1
    except (OSError, ValueError, IndexError):
        return False
    return False


if __name__ == "__main__":
    main()
