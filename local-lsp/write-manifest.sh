#!/usr/bin/env bash
# Write ~/.local/opt/tauceti-tools/MANIFEST.json: pins, toolchains, interpreters, sha256 of entry points.
set -euo pipefail
TOOLS="${TOOLS:-$HOME/.local/opt/tauceti-tools}"
export PATH="$HOME/.elan/bin:$PATH"
python3 - "$TOOLS" <<'EOF'
import hashlib, json, os, subprocess, sys, datetime, pathlib
tools = pathlib.Path(sys.argv[1])
def sha(p): return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def run(*a): return subprocess.run(a, capture_output=True, text=True).stdout.strip()
beam_src = tools / "beam/src"
beam_commit = run("git", "-C", str(beam_src), "rev-parse", "HEAD")
current = os.path.realpath(tools / "beam/share/current")
manifest = json.load(open(pathlib.Path(current) / "manifest.json")) if (pathlib.Path(current) / "manifest.json").is_file() else {}
bundles_root = tools / "beam/share/state/install-bundles/linux-x86_64"
bundles = []
for b in sorted(bundles_root.iterdir()) if bundles_root.is_dir() else []:
    meta = b / "metadata.json"
    if not meta.is_file():
        continue
    md = json.loads(meta.read_text())
    plugin = b / "workspace/.lake/build/lib/libbeam_Beam_LSP.so"
    bundles.append({"bundle_id": b.name, "toolchain": md.get("toolchain"), "lean_version": md.get("toolchainFingerprint", {}).get("leanVersion"),
                    "source_hash": md.get("sourceHash"), "plugin": str(plugin), "plugin_sha256": sha(plugin) if plugin.is_file() else None,
                    "daemon": str(b / "workspace/.lake/build/bin/beam-daemon")})
venv = tools / "uv-tools/lean-lsp-mcp"
py = os.path.realpath(venv / "bin/python")
ver = lambda pkg: run(str(venv / "bin/python"), "-c", f"import importlib.metadata as m; print(m.version('{pkg}'))")
out = {
  "written_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
  "host": os.uname().nodename,
  "beam": {
    "repo": "https://github.com/leanprover/lean-beam",
    "commit": beam_commit,
    "version": run(str(tools / "beam/bin/lean-beam"), "--version").splitlines()[0],
    "beam_own_toolchain": (beam_src / "lean-toolchain").read_text().strip(),
    "runtime_payload": pathlib.Path(current).name,
    "install_root": str(tools / "beam/share"),
    "bin": {n: {"path": str(tools / "beam/bin" / n), "sha256": sha(tools / "beam/bin" / n)} for n in ("lean-beam", "lean-beam-mcp", "lean-beam-search")},
    "libexec": {n: {"path": str(pathlib.Path(current) / "libexec" / n), "sha256": sha(pathlib.Path(current) / "libexec" / n)} for n in ("beam-cli", "beam-daemon", "lean-beam-mcp")},
    "bundles": bundles,
    "toolchains_built": ["leanprover/lean4:v4.34.0-rc2", "leanprover/lean4:v4.35.0-rc3"],
    "toolchain_admission": "release-line (compatible-lean-release-lines v4.34, v4.35); neither is in validated-lean-toolchains at this commit",
  },
  "lean_lsp_mcp": {
    "version": ver("lean-lsp-mcp"), "leanclient": ver("leanclient"), "mcp_sdk": ver("mcp"),
    "python": py, "python_version": run(py, "--version"),
    "venv": str(venv), "entry": {"path": str(tools / "bin/lean-lsp-mcp"), "resolves_to": os.path.realpath(tools / "bin/lean-lsp-mcp"),
                                 "sha256": sha(os.path.realpath(tools / "bin/lean-lsp-mcp"))},
    "installer": "uv tool install --python /usr/bin/python3 lean-lsp-mcp==<version> (UV_TOOL_DIR/UV_TOOL_BIN_DIR under this tree)",
    "uv": run("uv", "--version"),
  },
  "elan": {"home": os.path.expanduser("~/.elan"), "version": run("elan", "--version")},
  "sandbox_binds_ro": [str(tools), os.path.expanduser("~/.elan")],
}
(tools / "MANIFEST.json").write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps(out, indent=2))
EOF
