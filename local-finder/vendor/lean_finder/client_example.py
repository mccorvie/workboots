"""
Example client for the lean-finder retrieval server.

Start the server first:
    python server.py

Then run this script:
    python client_example.py

You can also use it as a library:
    from client_example import search
    results = search("Terminal Objects are Zero Objects", top_k=5)
"""

import argparse
import json
import urllib.error
import urllib.request
from typing import List

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8000
DEFAULT_VERSION = "v4.28.0"


# ---------------------------------------------------------------------------
# Client helpers
# ---------------------------------------------------------------------------

def _base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def health_check(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict:
    url = f"{_base_url(host, port)}/health"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def list_versions(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> dict:
    url = f"{_base_url(host, port)}/versions"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def search(
    query: str,
    top_k: int = 10,
    version: str = DEFAULT_VERSION,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> List[dict]:
    """
    Query the lean-finder server and return a list of result dicts.

    Each result has:
        score, formal_name, informal_name, kind, type,
        informal_description, path
    """
    payload = json.dumps({
        "inputs": query,
        "top_k": top_k,
        "version": version,
    }).encode()

    req = urllib.request.Request(
        f"{_base_url(host, port)}/search",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            return data.get("results", [])
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        raise RuntimeError(body.get("detail", str(e))) from e


# ---------------------------------------------------------------------------
# Pretty-print helper
# ---------------------------------------------------------------------------

def _print_results(query: str, results: List[dict]) -> None:
    print(f"\n{'='*60}")
    print(f"Query: {query!r}")
    print("="*60)
    if not results:
        print("  (no results)")
        return
    for i, r in enumerate(results, 1):
        print(f"  [{i}] score={r['score']:.4f}  {r['formal_name']}")
        print(f"       path : {r['path']}")
        print(f"       type : {r['type']}")
        if r.get("informal_description"):
            print(f"       desc : {r['informal_description'][:120]}")


# ---------------------------------------------------------------------------
# Demo queries
# ---------------------------------------------------------------------------

DEMO_QUERIES = [
    {
        "query": "For any two parallel morphisms $f, g : X \\to Y$ in a category $\\mathcal{C}$, the object assigned to `one` by the parallel pair functor $\\text{parallelPair}(f, g)$ is $Y$. That is, $\\text{parallelPair}(f, g)(\\text{one}) = Y$.",
        "top_k": 3,
        "version": "v4.19.0",
    },
    {
        "query": "Terminal Objects are Zero Objects",
        "top_k": 5,
        "version": "v4.28.0",
    },
]


def main(host: str, port: int) -> None:
    print(f"Connecting to lean-finder server at {host}:{port}")

    # Liveness check
    try:
        status = health_check(host, port)
        print(f"Health: {status}")
    except Exception as e:
        print(f"ERROR: Server not reachable — {e}")
        return

    # Available versions
    ver_info = list_versions(host, port)
    print(f"Available versions: {ver_info['versions']}  (default: {ver_info['default']})")

    # Run demo queries
    for item in DEMO_QUERIES:
        results = search(
            query=item["query"],
            top_k=item.get("top_k", 5),
            version=item.get("version", DEFAULT_VERSION),
            host=host,
            port=port,
        )
        _print_results(item["query"], results)

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="lean-finder client example")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    main(args.host, args.port)
