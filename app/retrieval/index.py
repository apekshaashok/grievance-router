"""Vector index over chunks: FAISS (inner product on normalised vectors), numpy fallback (L1)."""
import json
import os
import threading
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..config import cfg
from .chunking import Chunk
from .embedder import embed_query, embedder_id

try:
    import faiss  # type: ignore
except Exception:  # pragma: no cover
    faiss = None


class VectorIndex:
    def __init__(self, chunks: List[Chunk], vectors: np.ndarray, meta: dict):
        self.chunks = chunks
        self.by_id: Dict[str, Chunk] = {c.chunk_id: c for c in chunks}
        self.vectors = vectors
        self.meta = meta
        self._faiss = None
        if faiss is not None and len(chunks):
            self._faiss = faiss.IndexFlatIP(vectors.shape[1])
            self._faiss.add(vectors)

    def search(self, query: str, k: int = 4, regulator: Optional[str] = None,
               kind: Optional[str] = None) -> List[Tuple[Chunk, float]]:
        if not self.chunks:
            return []
        q = embed_query(query).reshape(1, -1)
        n = len(self.chunks)
        if self._faiss is not None:
            scores, ids = self._faiss.search(q, n)
            pairs = [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i >= 0]
        else:
            sims = (self.vectors @ q[0]).tolist()
            pairs = sorted(enumerate(sims), key=lambda x: -x[1])
        out = []
        for i, s in pairs:
            c = self.chunks[i]
            if regulator and c.regulator.upper() != regulator.upper():
                continue
            if kind and c.kind != kind:
                continue
            out.append((c, s))
            if len(out) >= k:
                break
        return out

    def get(self, chunk_id: str) -> Optional[Chunk]:
        if chunk_id in self.by_id:
            return self.by_id[chunk_id]
        cid = chunk_id.strip()
        if "#" not in cid:  # bare clause id: accept only if unambiguous
            hits = [c for c in self.chunks if c.clause_id == cid]
            return hits[0] if len(hits) == 1 else None
        return None

    def list_clauses(self, regulator: str, kind: Optional[str] = None) -> List[Chunk]:
        return [c for c in self.chunks if c.regulator.upper() == regulator.upper() and (kind is None or c.kind == kind)]


_loaded: Dict[str, Tuple[float, VectorIndex]] = {}
_lock = threading.Lock()


def index_paths(index_dir: str):
    return (os.path.join(index_dir, "chunks.jsonl"), os.path.join(index_dir, "vectors.npy"),
            os.path.join(index_dir, "index_meta.json"))


def index_exists(index_dir: Optional[str] = None) -> bool:
    return os.path.exists(index_paths(index_dir or cfg.INDEX_DIR)[2])


def load_index(index_dir: Optional[str] = None) -> VectorIndex:
    """Loads (and reloads when the pipeline publishes a newer index) the index."""
    d = index_dir or cfg.INDEX_DIR
    cpath, vpath, mpath = index_paths(d)
    if not os.path.exists(mpath):
        raise FileNotFoundError(f"no index at {d}; run `python -m app.ingest` first")
    mtime = os.path.getmtime(mpath)
    with _lock:
        cached = _loaded.get(d)
        if cached and cached[0] == mtime:
            return cached[1]
        with open(cpath) as f:
            chunks = [Chunk(**json.loads(l)) for l in f if l.strip()]
        vectors = np.load(vpath)
        with open(mpath) as f:
            meta = json.load(f)
        if meta.get("embedder_id") != embedder_id():
            raise RuntimeError(f"index was built with {meta.get('embedder_id')} but runtime embedder is "
                               f"{embedder_id()}; re-run ingestion")
        idx = VectorIndex(chunks, vectors, meta)
        _loaded[d] = (mtime, idx)
        return idx


def clear_cache():
    with _lock:
        _loaded.clear()


def publish_index_metrics(index_dir: Optional[str] = None):
    """Expose data freshness and pipeline throughput as metrics (D2)."""
    from ..metrics import INDEX_BUILT_TS, INDEX_CHUNKS, INGEST_THROUGHPUT
    try:
        meta = load_index(index_dir).meta
    except Exception:
        return
    INDEX_CHUNKS.set(meta.get("n_chunks", 0))
    INDEX_BUILT_TS.set(meta.get("built_at_epoch", 0))
    INGEST_THROUGHPUT.set(meta.get("throughput_chunks_per_s", 0))
