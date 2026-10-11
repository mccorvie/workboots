#!/usr/bin/env python3
"""TauCeti Lean Finder search service.

Wire protocol (workboots-plan.md, "Search service wire protocol"):
    GET /health            -> {"ok": true, "tool": "finder", "manifest": {...}}
    GET /search?q=&k=10    -> {"tool": "finder", "pin": "b3-...", "hits": [{name, kind, module, signature, description, score}]}
    errors                 -> HTTP 4xx/5xx with {"error": "..."}

Optional parameters: `pin=<name>` on both endpoints selects one of the configured
pins (default: --default-pin or the first --pin).  /health without `pin` returns
the service manifest, whose "pins" map holds each index's MANIFEST.json.

The model is loaded once; every configured pin's corpus.jsonl + corpus.index
is loaded at startup.  Query embedding follows upstream server.py exactly
(instruction prefix + the same tokenizer/pooling/normalisation as passages).
Sockets are bound only after everything is loaded, so a reachable socket means
ready.  Deterministic: exact FAISS inner-product search, no reranking, no
network access (HF_HUB_OFFLINE is forced on).

Usage:
    server.py --pin b3-11bc13335fb9 --unix-socket $XDG_RUNTIME_DIR/tauceti-finder/finder.sock
    server.py --pin b3-11bc13335fb9 --port 8090 --device cpu
Environment fallbacks: TAUCETI_FINDER_PINS (space/comma separated), TAUCETI_FINDER_DEVICE,
TAUCETI_FINDER_DTYPE, TAUCETI_FINDER_MODEL_DIR, TAUCETI_FINDER_DATA (index root), TAUCETI_FINDER_PORT.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import stat
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import numpy as np  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

from finder_common import (  # noqa: E402
    DATA_ROOT, EMBEDDING_DIM, LEAN_FINDER_COMMIT, Embedder, default_model_dir, package_versions,
    read_jsonl, resolve_device, sha256_file,
)
from fetch_model import MODEL_REPO, MODEL_REVISION  # noqa: E402

TOOL = "finder"
TAUCETI_HEADERS = ("x-tauceti-worker", "x-tauceti-phase", "x-tauceti-agent", "x-tauceti-model", "x-tauceti-round")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


class PinIndex:
    def __init__(self, name: str, directory: Path, verify: bool = True):
        import faiss
        self.name = name
        self.dir = directory
        manifest_path = directory / "MANIFEST.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"{manifest_path} missing (build it with build_finder.py)")
        self.manifest = json.loads(manifest_path.read_text())
        corpus_path, index_path = directory / "corpus.jsonl", directory / "corpus.index"
        if verify:
            for fn, p in (("corpus.jsonl", corpus_path), ("corpus.index", index_path)):
                want = self.manifest.get("files", {}).get(fn, {}).get("sha256")
                got = sha256_file(p)
                if want and want != got:
                    raise RuntimeError(f"{p}: sha256 {got} != manifest {want}")
        self.corpus = read_jsonl(corpus_path)
        self.index = faiss.read_index(str(index_path))
        if self.index.ntotal != len(self.corpus):
            raise RuntimeError(f"{directory}: index has {self.index.ntotal} rows, corpus {len(self.corpus)}")
        if self.index.d != EMBEDDING_DIM:
            raise RuntimeError(f"{directory}: index dim {self.index.d} != {EMBEDDING_DIM}")
        self.pin = self.manifest.get("pin", name)

    def hit(self, row: dict, score: float) -> dict:
        module = row.get("module") or (row.get("path") or "").replace("/", ".")
        return {
            "name": row.get("name", ""),
            "kind": row.get("kind_source") or row.get("kind", ""),
            "module": module,
            "signature": row.get("typeFull", ""),
            "description": "",
            "score": float(score),
        }


class Service:
    def __init__(self, model_dir: Path, device: str, dtype: str, pins: dict[str, Path], default_pin: str | None,
                 verify: bool, max_k: int):
        self.started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log(f"loading model {model_dir} on {device} ({dtype})")
        self.embedder = Embedder(model_dir, device=device, dtype=dtype)
        log(f"model ready in {self.embedder.load_seconds:.0f}s")
        self.pins: dict[str, PinIndex] = {}
        for name, d in pins.items():
            t0 = time.time()
            self.pins[name] = PinIndex(name, d, verify=verify)
            log(f"loaded pin {name}: {self.pins[name].index.ntotal} rows from {d} in {time.time()-t0:.1f}s")
        self.default_pin = default_pin or next(iter(self.pins))
        if self.default_pin not in self.pins:
            raise SystemExit(f"--default-pin {self.default_pin} is not among {list(self.pins)}")
        self.max_k = max_k
        self.lock = threading.Lock()
        self.model_dir, self.device, self.dtype = str(model_dir), device, dtype
        # Warm up (first forward pass allocates kernels/caches) and fix the dim.
        qv = self.embedder.embed_query("warm up")
        assert qv.shape == (1, EMBEDDING_DIM), qv.shape

    def manifest(self) -> dict:
        return {
            "tool": TOOL,
            "started_at": self.started_at,
            "model": {"repo": MODEL_REPO, "revision": MODEL_REVISION, "dir": self.model_dir},
            "lean_finder_commit": LEAN_FINDER_COMMIT,
            "device": self.device,
            "dtype": self.dtype,
            "embedding_dim": EMBEDDING_DIM,
            "default_pin": self.default_pin,
            "pins": {name: p.manifest for name, p in self.pins.items()},
            "versions": package_versions(),
        }

    def pin_manifest(self, p: "PinIndex") -> dict:
        """Service manifest with the pin's identity fields at the top level (per-pin socket /health)."""
        m = self.manifest()
        m.update({k: p.manifest.get(k) for k in ("pin", "tauceti_sha", "mathlib_rev", "lean_toolchain", "decls_sha256")})
        m["pin"] = p.pin
        m["default_pin"] = p.name
        m["rows"] = p.manifest.get("rows")
        m["built_at"] = p.manifest.get("built_at")
        return m

    def resolve_pin(self, name: str | None) -> PinIndex:
        if not name:
            return self.pins[self.default_pin]
        if name in self.pins:
            return self.pins[name]
        for p in self.pins.values():  # allow the manifest pin as an alias of the directory name
            if p.pin == name:
                return p
        raise KeyError(name)

    def search(self, q: str, k: int, pin: PinIndex) -> dict:
        with self.lock:
            qv = self.embedder.embed_query(q)                     # upstream server.py:119-120
        scores, ids = pin.index.search(np.ascontiguousarray(qv, dtype=np.float32), k)  # upstream server.py:122
        hits = [pin.hit(pin.corpus[int(i)], s) for i, s in zip(ids[0], scores[0]) if int(i) >= 0]
        return {"tool": TOOL, "pin": pin.pin, "hits": hits}


app = FastAPI(title="tauceti-finder", docs_url=None, redoc_url=None, openapi_url=None)


def error(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


@app.exception_handler(StarletteHTTPException)
async def _http_exc(_: Request, exc: StarletteHTTPException):
    return error(exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def _validation_exc(_: Request, exc: RequestValidationError):
    return error(400, f"bad request: {exc.errors()[0].get('msg', 'invalid parameters') if exc.errors() else 'invalid parameters'}")


@app.exception_handler(Exception)
async def _any_exc(_: Request, exc: Exception):
    log(f"internal error: {exc!r}")
    return error(500, f"internal error: {type(exc).__name__}")


@app.get("/health")
def health(request: Request, pin: str | None = None):
    svc: Service = request.app.state.service
    if pin:
        try:
            p = svc.resolve_pin(pin)
        except KeyError:
            return error(404, f"unknown pin {pin!r}; configured: {sorted(svc.pins)}")
        return {"ok": True, "tool": TOOL, "manifest": p.manifest}
    sock_pin = request.scope.get("tauceti_pin")
    if sock_pin:  # per-pin socket: the socket's own identity fields at the top level (plan, contract amendments)
        return {"ok": True, "tool": TOOL, "manifest": svc.pin_manifest(svc.resolve_pin(sock_pin))}
    return {"ok": True, "tool": TOOL, "manifest": svc.manifest()}


@app.get("/search")
def search(request: Request, q: str | None = None, k: int = 10, pin: str | None = None):
    svc: Service = request.app.state.service
    query = (q or "").strip()                                        # upstream server.py:108
    if not query:
        return error(400, "missing or empty query parameter q")
    if k < 1 or k > svc.max_k:
        return error(400, f"k must be between 1 and {svc.max_k}")
    try:
        p = svc.resolve_pin(pin or request.scope.get("tauceti_pin"))
    except KeyError:
        return error(404, f"unknown pin {pin!r}; configured: {sorted(svc.pins)}")
    t0 = time.time()
    result = svc.search(query, k, p)
    ms = int((time.time() - t0) * 1000)
    ctx = " ".join(f"{h[10:]}={request.headers[h]}" for h in TAUCETI_HEADERS if h in request.headers)
    log(f"search pin={p.name} k={k} ms={ms} hits={len(result['hits'])} {ctx} q={query[:120]!r}")
    return result


# ---------------------------------------------------------------------------

def parse_pins(values: list[str] | None) -> list[str]:
    if values:
        return values
    env = os.environ.get("TAUCETI_FINDER_PINS", "")
    return [p for p in env.replace(",", " ").split() if p]


class PinScope:
    """ASGI wrapper recording which pin the listening socket belongs to."""

    def __init__(self, inner, pin: str):
        self.inner, self.pin = inner, pin

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope["tauceti_pin"] = self.pin
        await self.inner(scope, receive, send)


def prepare_unix_socket(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o755)
    if path.exists():
        if stat.S_ISSOCK(path.stat().st_mode):
            path.unlink()
        else:
            raise SystemExit(f"{path} exists and is not a socket")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pin", action="append", help="pin directory name under --data-dir (repeatable; or TAUCETI_FINDER_PINS)")
    ap.add_argument("--default-pin", default=os.environ.get("TAUCETI_FINDER_DEFAULT_PIN") or None)
    ap.add_argument("--data-dir", type=Path, default=DATA_ROOT, help="root holding <pin>/corpus.{jsonl,index}")
    ap.add_argument("--model-dir", type=Path, default=Path(os.environ["TAUCETI_FINDER_MODEL_DIR"]) if os.environ.get("TAUCETI_FINDER_MODEL_DIR") else None)
    ap.add_argument("--device", default=os.environ.get("TAUCETI_FINDER_DEVICE") or None)
    ap.add_argument("--dtype", choices=["bfloat16", "float32"], default=os.environ.get("TAUCETI_FINDER_DTYPE") or None)
    ap.add_argument("--unix-socket", type=Path, default=None, help="serve HTTP on this unix socket (dir 0755, socket 0666)")
    ap.add_argument("--socket-root", type=Path, default=None,
                    help="serve one unix socket per pin at <root>/<pin>/finder.sock (dirs 0755, sockets 0666); "
                         "each socket defaults to its own pin")
    ap.add_argument("--port", type=int, default=int(os.environ["TAUCETI_FINDER_PORT"]) if os.environ.get("TAUCETI_FINDER_PORT") else None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--max-k", type=int, default=100)
    ap.add_argument("--no-verify", action="store_true", help="skip sha256 verification of index files at startup")
    args = ap.parse_args(argv)

    pins = parse_pins(args.pin)
    if not pins:
        ap.error("no pins: pass --pin or set TAUCETI_FINDER_PINS")
    if args.unix_socket is None and args.port is None and args.socket_root is None:
        ap.error("pass --socket-root DIR, --unix-socket PATH and/or --port N")

    device = resolve_device(args.device)
    dtype = args.dtype or ("bfloat16" if device.startswith("cuda") else "float32")
    model_dir = args.model_dir or default_model_dir()
    if not (model_dir / "config.json").exists():
        raise SystemExit(f"model snapshot missing at {model_dir}; run fetch_model.py")
    pin_dirs = {p: args.data_dir / p for p in pins}
    for p, d in pin_dirs.items():
        if not (d / "MANIFEST.json").exists():
            raise SystemExit(f"pin {p}: {d}/MANIFEST.json missing")

    app.state.service = Service(model_dir, device, dtype, pin_dirs, args.default_pin, not args.no_verify, args.max_k)

    configs = []
    pin_sockets: list[Path] = []
    if args.socket_root is not None:
        args.socket_root.mkdir(parents=True, exist_ok=True)
        os.chmod(args.socket_root, 0o755)
        for name in pin_dirs:
            sp = args.socket_root / name / "finder.sock"
            prepare_unix_socket(sp)
            pin_sockets.append(sp)
            configs.append(uvicorn.Config(PinScope(app, name), uds=str(sp), log_level="warning", workers=1))
    if args.unix_socket is not None:
        prepare_unix_socket(args.unix_socket)
        configs.append(uvicorn.Config(app, uds=str(args.unix_socket), log_level="warning", workers=1))
    if args.port is not None:
        configs.append(uvicorn.Config(app, host=args.host, port=args.port, log_level="warning", workers=1))

    servers = [uvicorn.Server(c) for c in configs]
    threads = []
    for s in servers:  # one thread per bind; uvicorn skips signal handling off the main thread
        t = threading.Thread(target=lambda srv=s: asyncio.run(srv.serve()), daemon=True)
        t.start()
        threads.append(t)

    stop = threading.Event()

    def _stop(signum, _frame):
        log(f"signal {signum}: shutting down")
        for s in servers:
            s.should_exit = True
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    # Wait until the binds are live, then report.
    deadline = time.time() + 30
    while time.time() < deadline and not all(getattr(s, "started", False) for s in servers):
        time.sleep(0.05)
    if not all(getattr(s, "started", False) for s in servers) or not all(t.is_alive() for t in threads):
        log("error: a listener failed to start (see traceback above); exiting")
        for s in servers:
            s.should_exit = True
        return 1
    if args.unix_socket is not None and args.unix_socket.exists():
        os.chmod(args.unix_socket, 0o666)  # uvicorn already does this; belt and braces
    for sp in pin_sockets:
        if sp.exists():
            os.chmod(sp, 0o666)
    where = [str(x) for x in pin_sockets] + ([str(args.unix_socket)] if args.unix_socket else [])
    where += [f"http://{args.host}:{args.port}"] if args.port else []
    log(f"ready: pins={list(pin_dirs)} default={app.state.service.default_pin} serving on {', '.join(where)}")
    for t in threads:
        while t.is_alive():
            t.join(0.5)
    for sp in pin_sockets + ([args.unix_socket] if args.unix_socket is not None else []):
        try:
            if sp.exists():
                sp.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
