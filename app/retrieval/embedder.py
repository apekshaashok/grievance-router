"""Embedders (L1). `hash` needs no download and is deterministic (used in tests/CI and by default);
`sentence-transformers` is the real one (set EMBEDDER and EMBED_MODEL at runtime)."""
import re
import zlib
from functools import lru_cache
from typing import List

import numpy as np

from ..config import cfg

STOP = set("""a an and are as at be been but by for from has have i in is it its my of on or our so than that the their
there this to was were will with you your me we they not no do does did am can could would should may might also if then""".split())
_TOK = re.compile(r"[a-z0-9]+")


def _norm(tok: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 4:
            return tok[: -len(suf)]
    return tok


def tokens(text: str) -> List[str]:
    return [_norm(t) for t in _TOK.findall(text.lower()) if t not in STOP]


class HashEmbedder:
    """Feature-hashing bag of unigrams and bigrams, L2-normalised. Lexical, fast, no network."""

    def __init__(self, dim: int):
        self.dim = dim
        self.id = f"hash-{dim}"

    def encode(self, texts: List[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype="float32")
        for r, text in enumerate(texts):
            toks = tokens(text)
            for t in toks:
                h = zlib.crc32(t.encode())
                out[r, h % self.dim] += 1.0 if (h >> 16) & 1 else -1.0
            for a, b in zip(toks, toks[1:]):
                h = zlib.crc32((a + "_" + b).encode())
                out[r, h % self.dim] += (0.5 if (h >> 16) & 1 else -0.5)
            n = np.linalg.norm(out[r])
            if n:
                out[r] /= n
        return out


class STEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # optional heavy dependency
        self.model = SentenceTransformer(model_name)
        getdim = getattr(self.model, "get_embedding_dimension", None) or self.model.get_sentence_embedding_dimension
        self.dim = getdim()
        self.id = f"st-{model_name}"

    def encode(self, texts: List[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, normalize_embeddings=True), dtype="float32")


_cache = {}


def embedder_id() -> str:
    """The id of the configured embedder, computed without loading the model (the API never needs the model)."""
    if cfg.EMBEDDER == "hash":
        return f"hash-{cfg.EMBED_DIM}"
    if cfg.EMBEDDER == "sentence-transformers":
        return f"st-{cfg.require('EMBED_MODEL')}"
    raise RuntimeError(f"unknown EMBEDDER {cfg.EMBEDDER!r}")


def get_embedder():
    key = (cfg.EMBEDDER, cfg.EMBED_MODEL, cfg.EMBED_DIM)
    if key not in _cache:
        if cfg.EMBEDDER == "hash":
            _cache[key] = HashEmbedder(cfg.EMBED_DIM)
        elif cfg.EMBEDDER == "sentence-transformers":
            _cache[key] = STEmbedder(cfg.require("EMBED_MODEL"))
        else:
            raise RuntimeError(f"unknown EMBEDDER {cfg.EMBEDDER!r}")
    return _cache[key]


@lru_cache(maxsize=2048)
def _cached_query_vec(embedder_id: str, text: str) -> bytes:
    return get_embedder().encode([text])[0].tobytes()


def embed_query(text: str) -> np.ndarray:
    """Query embeddings are cached (D7): agents re-issue near-identical queries."""
    from ..metrics import CACHE
    emb = get_embedder()
    before = _cached_query_vec.cache_info().hits
    raw = _cached_query_vec(emb.id, text)
    CACHE.labels("embedding", "hit" if _cached_query_vec.cache_info().hits > before else "miss").inc()
    return np.frombuffer(raw, dtype="float32")
