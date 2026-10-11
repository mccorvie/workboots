"""Shared pieces for build_finder.py and server.py.

Everything that determines *what text gets embedded and how* is taken from the
vendored Lean Finder sources (vendor/lean_finder, upstream commit 69b7646):

  format_entry(obj)          vendor/lean_finder/build_index.py:36   corpus row -> passage text
  last_token_pool(h, mask)   vendor/lean_finder/build_index.py:22   (== server.py:47)
  get_detailed_instruct(q)   vendor/lean_finder/server.py:60        query instruction prefix

Embedder below reproduces, line for line, the model-load and encode sequence of
upstream build_index.build_one() / server.EndpointHandler._init_model() and
_get_embeddings(); the only change is that the device (and optionally the
dtype) is a parameter instead of the literal "cuda".
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vendor.lean_finder import build_index as _lf_build  # noqa: E402
from vendor.lean_finder import server as _lf_server  # noqa: E402

format_entry = _lf_build.format_entry
last_token_pool = _lf_build.last_token_pool
get_detailed_instruct = _lf_server.get_detailed_instruct

LEAN_FINDER_COMMIT = "69b764614453057013e411a8c8081f447cda64e7"
MAX_LENGTH = 8192  # upstream: tokenizer(..., truncation=True, max_length=8192) for both passages and queries
EMBEDDING_DIM = 4096  # Qwen3 hidden_size in the model's config.json; asserted after the first encode

DATA_ROOT = Path(os.environ.get("TAUCETI_FINDER_DATA", Path.home() / ".local/share/tauceti-finder"))
INDEX_ROOT = Path(os.environ.get("TAUCETI_INDEX_DATA", Path.home() / ".local/share/tauceti-index"))
CACHE_ROOT = DATA_ROOT / "cache"


# ---------------------------------------------------------------------------
# Corpus adaptation: decls.jsonl (shared extraction) -> Lean Finder corpus rows
# ---------------------------------------------------------------------------

# Lean Finder corpus schema (upstream README): path, kind, name, typeFull,
# informal_name, informal_description, docString.  The two informal fields are
# never embedded (format_entry ignores them); we carry a few extra fields for
# our own wire protocol (module, line, is_mathlib, hash).  Unknown fields are
# ignored by upstream code.
#
# docString: in the upstream corpora an absent docstring is JSON null (164,717
# of 224,722 rows in mathlib4_28_0.jsonl; only 3 rows are ""), and format_entry
# renders null as the literal text 'docstring': 'None' via str(obj.get(field)).
# The shipped index was built from exactly that text, so we map an empty or
# missing docstring to None to land on the same passage text.  (FORMAT-CHECK.md)
#
# path: upstream uses the module path with slashes and no extension
# ("Mathlib/Topology/Compactness/Compact"); upstream server.py turns it back
# into a module name with replace("/", ".").  We store module -> path the same way.

# kind: upstream rows carry jixia's ConstantInfo-derived kinds (theorem 179,271 /
# definition 44,912 / inductive 304 / opaque 235 in mathlib4_28_0.jsonl); instances,
# abbrevs and lemmas are "definition"/"theorem" there.  The kind is embedded, so we
# map doc-gen4 / source-level kinds onto that vocabulary.  Unknown kinds pass through.
KIND_MAP = {
    "theorem": "theorem", "lemma": "theorem", "thm": "theorem",
    "def": "definition", "definition": "definition", "instance": "definition",
    "abbrev": "definition", "abbreviation": "definition",
    "structure": "inductive", "class": "inductive", "inductive": "inductive",
    "classinductive": "inductive", "class inductive": "inductive",
    "opaque": "opaque", "axiom": "axiom",
    "ctor": "constructor", "constructor": "constructor",
    "recursor": "recursor", "quot": "quotient", "quotient": "quotient",
}


def normalize_kind(kind: str | None) -> str:
    k = (kind or "").strip()
    return KIND_MAP.get(k.lower(), k)


# ---------------------------------------------------------------------------
# signature -> typeFull
# ---------------------------------------------------------------------------
#
# Lean Finder's typeFull is `PrettyPrinter.ppExpr` of the constant's *type*
# (jixia Analyzer/Process/Symbol.lean): `∀ {X : Type u} [inst : TopologicalSpace X] {s : Set X},
# IsCompact s → ...` for Props, `{α : Type u} → [PseudoMetricSpace α] → α → ℝ → Set α` for the
# rest, width 120, 2-space continuation indent.  The shared extraction's `signature` is doc-gen4's
# header instead: `<kind words> <name> <binders> [extends ...] : <type>` (see FORMAT-CHECK.md).
# When a decl row carries a real `typeFull`/`type` field we use it verbatim.  Otherwise
# TAUCETI_FINDER_SIGNATURE_MODE decides:
#   forall  (default) rebuild the pi-type text from the header: Prop kinds -> `∀ binders, A → B`,
#           others -> `b₁ → b₂ → T`; explicit binders whose names are not mentioned later become
#           bare arrows, as Lean's delaborator does.  Instance binders stay anonymous (`[C]`),
#           which is one of the two forms upstream has (8k anonymous vs 161k `[inst : C]` rows).
#   strip   drop only the `<kind words> <name> ` prefix (keeps `binders : type`)
#   raw     pass the signature through unchanged
# A signature that does not start with `<kind words> <name>` (e.g. upstream rows replayed through
# scripts/upstream_to_decls.py) is always passed through unchanged.

import re

SIGNATURE_MODE = os.environ.get("TAUCETI_FINDER_SIGNATURE_MODE", "forall")
_OPEN, _CLOSE = "({[⦃", ")}]⦄"
_IDENT = r"[^\s()\[\]{}⦃⦄,:→]"  # anything that can continue a Lean identifier for our purposes
_IDENT_NODOT = r"[^\s()\[\]{}⦃⦄,:→.]"


def _depth0_find(s: str, needle: str, start: int = 0) -> int:
    depth = 0
    i = start
    while i < len(s):
        c = s[i]
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth = max(0, depth - 1)
        elif depth == 0 and s.startswith(needle, i):
            return i
        i += 1
    return -1


def _split_binders(text: str) -> list[str]:
    out: list[str] = []
    depth = 0
    cur = ""
    for ch in text:
        if ch in _OPEN:
            if depth == 0 and cur.strip():
                out.append(cur.strip())
                cur = ""
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
        cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def split_signature(signature: str, name: str):
    """doc-gen4 header -> (kind_words, binders, parents, type_text) or None if it is not one."""
    m = re.match(r"^((?:[a-z]+\s+){0,3}?)" + re.escape(name) + r"(?=\s|$)", signature)
    if not m:
        return None
    rest = signature[m.end():]
    j = _depth0_find(rest, " : ")
    if j < 0:
        if rest.startswith(" :") or rest.strip() == "":
            return (m.group(1).strip(), [], None, rest.lstrip(" :").strip())
        return None
    binders_text, type_text = rest[:j].strip(), rest[j + 3:]
    parents = None
    k = _depth0_find(binders_text, "extends ")
    if k >= 0:
        parents, binders_text = binders_text[k + 8:].strip(), binders_text[:k].strip()
    return (m.group(1).strip(), _split_binders(binders_text), parents, type_text)


def _parse_binder(b: str):
    bracket, inner = b[0], b[1:-1]
    j = _depth0_find(inner, " : ")
    if j < 0:
        return bracket, [], inner.strip()
    return bracket, inner[:j].split(), inner[j + 3:].strip()


def _mentions(names: list[str], later: str) -> bool:
    for n in names:
        # `.` may follow a mention (`xs.reverse`, `h.1`) but never precedes one (`Foo.xs`).
        if re.search(r"(?<!" + _IDENT + r")" + re.escape(n) + r"(?!" + _IDENT_NODOT + r")", later):
            return True
    return False


PROP_KINDS = {"theorem", "axiom"}


def signature_to_type_full(signature: str, name: str, kind: str, mode: str | None = None) -> tuple[str, str]:
    """Return (typeFull_text, how) with how in {"raw", "strip", "forall", "passthrough"}."""
    mode = mode or SIGNATURE_MODE
    if mode == "raw":
        return signature, "raw"
    parts = split_signature(signature, name)
    if parts is None:
        return signature, "passthrough"
    _, binders, _parents, type_text = parts
    if mode == "strip":
        return ((" ".join(binders) + " : " + type_text) if binders else type_text), "strip"
    if not binders:
        return type_text, "forall"
    parsed = [_parse_binder(b) for b in binders]
    # items: ("arrow", type) for an unused explicit binder, ("bind", bracket, names, type) otherwise.
    items: list[tuple] = []
    for i, (bracket, names, typ) in enumerate(parsed):
        later = " ".join(binders[i + 1:]) + " " + type_text
        if bracket == "(" and not _mentions(names, later):
            items.append(("arrow", typ))
        elif (items and items[-1][0] == "bind" and items[-1][1] == bracket and items[-1][3] == typ
              and names and items[-1][2] and bracket != "["):
            items[-1] = ("bind", bracket, items[-1][2] + names, typ)   # Lean merges `(a : T) (b : T)` into `(a b : T)`
        else:
            items.append(("bind", bracket, names, typ))

    def render(it: tuple) -> str:
        if it[0] == "bind":
            _, bracket, names, typ = it
            body = (" ".join(names) + " : " + typ) if names else typ
            return bracket + body + _CLOSE[_OPEN.index(bracket)]
        return it[1]

    out = ""
    if kind in PROP_KINDS:
        group: list[str] = []
        for it in items:
            if it[0] == "bind":
                group.append(render(it))
            else:
                if group:
                    out += "∀ " + " ".join(group) + ", "
                    group = []
                out += it[1] + " → "
        if group:
            out += "∀ " + " ".join(group) + ", "
        return out + type_text, "forall"
    for it in items:
        out += render(it) + " → "
    return out + type_text, "forall"


def adapt_decl(d: dict) -> dict:
    module = d.get("module") or ""
    file = d.get("file") or ""
    if not module and file:
        module = (file[:-5] if file.endswith(".lean") else file).replace("/", ".")
    doc = d.get("docstring")
    kind = normalize_kind(d.get("kind"))
    name = d.get("name") or ""
    tf = d.get("type_full") or d.get("typeFull") or d.get("type")
    if tf:
        type_full, how = tf, "typeFull"
    else:
        type_full, how = signature_to_type_full(d.get("signature") or "", name, kind)
    return {
        "path": module.replace(".", "/"),
        "kind": kind,
        "kind_source": d.get("kind") or "",
        "name": name,
        "typeFull": type_full,
        "signature": d.get("signature") or "",
        "type_from": how,
        "informal_name": "",
        "informal_description": "",
        "docString": doc if doc else None,
        "module": module,
        "file": file,
        "line": d.get("line"),
        "is_mathlib": d.get("is_mathlib"),
        "hash": d.get("hash"),
    }


def entry_key(text: str) -> str:
    """Cache key: sha256 of the exact text handed to the tokenizer."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_jsonl(path: Path, limit: int | None = None, stride: int = 1) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if stride > 1 and i % stride:
                continue
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
            if limit is not None and len(rows) >= limit:
                break
    return rows


def sha256_file(path: Path, bufsize: int = 1 << 22) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def default_model_dir() -> Path:
    from fetch_model import model_dir  # local import: keeps huggingface_hub out of the server's import path
    return model_dir()


def resolve_device(device: str | None) -> str:
    import torch
    if device:
        return device
    return "cuda:0" if torch.cuda.is_available() else "cpu"


def device_kind(device: str) -> str:
    return "cuda" if device.startswith("cuda") else "cpu"


class Embedder:
    """Lean Finder encoder: identical to upstream apart from the device parameter.

    dtype: upstream always uses torch.bfloat16.  "float32" is offered for CPU
    runs on hardware without native bf16 matmuls (see VALIDATION.md for the
    measured agreement between fp32-CPU and the upstream bf16-GPU vectors).
    """

    def __init__(self, model_dir: Path | str, device: str = "cuda:0", dtype: str = "bfloat16"):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.model_dir = str(model_dir)
        self.device = device
        self.dtype_name = dtype
        torch_dtype = {"bfloat16": torch.bfloat16, "float32": torch.float32, "float16": torch.float16}[dtype]
        t0 = time.time()
        # upstream build_index.py:49 / server.py:85
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_dir, padding_side="left", fix_mistral_regex=True)
        # upstream build_index.py:52 / server.py:88  (torch_dtype=torch.bfloat16 there)
        model = AutoModel.from_pretrained(self.model_dir, torch_dtype=torch_dtype, trust_remote_code=True)
        # upstream server.py:89-90
        if model.config.pad_token_id is None:
            model.config.pad_token_id = 0
        # upstream build_index.py:53 / server.py:91  (.to("cuda") there)
        self.model = model.eval().to(device)
        self.load_seconds = time.time() - t0
        self.dim = int(self.model.config.hidden_size)

    def tokenize(self, texts: Sequence[str]):
        # upstream build_index.py:71 / server.py:97
        return self.tokenizer(list(texts), padding=True, truncation=True, max_length=MAX_LENGTH, return_tensors="pt")

    def token_lengths(self, texts: Iterable[str]) -> list[int]:
        enc = self.tokenizer(list(texts), padding=False, truncation=True, max_length=MAX_LENGTH)
        return [len(x) for x in enc["input_ids"]]

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        import torch
        import torch.nn.functional as F

        inputs = self.tokenize(texts)
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = self.model(**inputs)
        # upstream build_index.py:75-76 / server.py:103-104
        emb = last_token_pool(outputs.last_hidden_state, inputs["attention_mask"]).float()
        emb = F.normalize(emb, p=2, dim=1)
        return emb.cpu().numpy().astype(np.float32, copy=False)

    def embed_query(self, query: str) -> np.ndarray:
        # upstream server.py:119-120
        return self.embed([get_detailed_instruct(query)])


# ---------------------------------------------------------------------------
# Embedding cache: sha256(format_entry(row)) -> float32[dim]
# ---------------------------------------------------------------------------
#
# Layout: CACHE_ROOT/<model_rev12>/<cuda|cpu>-<dtype>/pack-<utc>-<pid>.{keys,f32}
#   .keys  one 64-hex key per line, in row order
#   .f32   raw little-endian float32 rows (N x dim), appended in the same order
# Packs are append-only; a pack whose .f32 is shorter than its .keys (crash
# mid-write) is truncated to the complete rows on open.  Vectors from different
# devices/dtypes are kept apart because bf16 GPU and fp32 CPU numerics differ
# slightly; an index should be built from one bucket only.

@dataclass
class _Pack:
    keys_path: Path
    vec_path: Path
    n: int
    mm: np.memmap | None = None


class EmbeddingCache:
    def __init__(self, model_revision: str, device: str, dtype: str, dim: int = EMBEDDING_DIM,
                 root: Path = CACHE_ROOT):
        self.dim = dim
        self.dir = root / model_revision[:12] / f"{device_kind(device)}-{dtype}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.packs: list[_Pack] = []
        self.where: dict[str, tuple[int, int]] = {}
        self._writer: tuple[Path, Path, object, object] | None = None
        self._load()

    # -- reading ----------------------------------------------------------
    def _load(self) -> None:
        row_bytes = self.dim * 4
        for keys_path in sorted(self.dir.glob("pack-*.keys")):
            vec_path = keys_path.with_suffix(".f32")
            if not vec_path.exists():
                continue
            keys = keys_path.read_text().split()
            n_vec = vec_path.stat().st_size // row_bytes
            n = min(len(keys), n_vec)
            if n == 0:
                continue
            pack = _Pack(keys_path, vec_path, n)
            idx = len(self.packs)
            self.packs.append(pack)
            for i, k in enumerate(keys[:n]):
                self.where[k] = (idx, i)  # later packs win

    def __len__(self) -> int:
        return len(self.where)

    def __contains__(self, key: str) -> bool:
        return key in self.where

    def _mm(self, pack: _Pack) -> np.memmap:
        if pack.mm is None or pack.mm.shape[0] != pack.n:
            pack.mm = np.memmap(pack.vec_path, dtype=np.float32, mode="r", shape=(pack.n, self.dim))
        return pack.mm

    def get(self, key: str) -> np.ndarray | None:
        loc = self.where.get(key)
        if loc is None:
            return None
        return np.asarray(self._mm(self.packs[loc[0]])[loc[1]])

    def gather(self, keys: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
        """Return (vectors, hit_mask) for keys; rows of missing keys are zero."""
        out = np.zeros((len(keys), self.dim), dtype=np.float32)
        hit = np.zeros(len(keys), dtype=bool)
        for i, k in enumerate(keys):
            v = self.get(k)
            if v is not None:
                out[i] = v
                hit[i] = True
        return out, hit

    # -- writing ----------------------------------------------------------
    def _open_writer(self):
        if self._writer is None:
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            base = self.dir / f"pack-{stamp}-{os.getpid()}"
            keys_path, vec_path = base.with_suffix(".keys"), base.with_suffix(".f32")
            kf = open(keys_path, "a", encoding="ascii")
            vf = open(vec_path, "ab")
            pack = _Pack(keys_path, vec_path, 0)
            self.packs.append(pack)
            self._writer = (keys_path, vec_path, kf, vf)
            self._writer_pack = len(self.packs) - 1
        return self._writer

    def put_many(self, keys: Sequence[str], vecs: np.ndarray) -> None:
        assert vecs.shape == (len(keys), self.dim), vecs.shape
        _, _, kf, vf = self._open_writer()
        pack = self.packs[self._writer_pack]
        vf.write(np.ascontiguousarray(vecs, dtype="<f4").tobytes())
        vf.flush()
        kf.write("".join(k + "\n" for k in keys))
        kf.flush()
        for k in keys:
            self.where[k] = (self._writer_pack, pack.n)
            pack.n += 1
            pack.mm = None

    def flush(self) -> None:
        if self._writer is not None:
            _, _, kf, vf = self._writer
            vf.flush(); os.fsync(vf.fileno())
            kf.flush(); os.fsync(kf.fileno())

    def close(self) -> None:
        if self._writer is not None:
            self.flush()
            _, _, kf, vf = self._writer
            kf.close(); vf.close()
            self._writer = None


def package_versions() -> dict:
    import faiss, torch, transformers  # noqa: E401
    return {
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "faiss": getattr(faiss, "__version__", "unknown"),
        "numpy": np.__version__,
    }
