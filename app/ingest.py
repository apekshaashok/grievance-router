"""Idempotent ingestion pipeline (D2): read -> parse -> chunk -> embed -> index.

    python -m app.ingest                 # build/refresh the index (safe to re-run)
    python -m app.ingest --only RBI-IOS  # force re-embedding of one document
    python -m app.ingest --force         # re-embed everything
    python -m app.ingest --dump          # print the chunks that would be indexed
    python -m app.ingest --pdf-to-text corpus/x.pdf

Idempotent: chunks are keyed by a content hash. Running twice yields the same index; only
new/changed chunks are embedded (re-embedding does not rebuild the whole index, L1).
"""
import argparse
import hashlib
import json
import os
import sys
import time
from typing import Dict, List

import numpy as np

from .config import cfg
from .logging_setup import log, setup_logging
from .retrieval.chunking import Chunk, parse_document
from .retrieval.embedder import get_embedder
from .retrieval.index import index_paths

import logging
logger = logging.getLogger("ingest")


def read_documents(corpus_dir: str) -> List[dict]:
    docs = []
    manifest = {}
    mpath = os.path.join(corpus_dir, "manifest.json")
    if os.path.exists(mpath):
        manifest = json.load(open(mpath))
    for name in sorted(os.listdir(corpus_dir)):
        path = os.path.join(corpus_dir, name)
        if name.lower().endswith((".txt", ".md")) and name.lower() != "readme.md":
            text = open(path, encoding="utf-8").read()
        else:
            continue
        docs.append({"file": name, "sha256": hashlib.sha256(text.encode()).hexdigest(), "text": text,
                     "meta": manifest.get(name, {})})
    if not docs:
        raise SystemExit(f"no .txt/.md corpus files in {corpus_dir}")
    return docs


def pdf_to_text(path: str) -> str:
    from pypdf import PdfReader
    out = []
    for i, page in enumerate(PdfReader(path).pages, 1):
        out.append(f"# ---- page {i} ----\n{page.extract_text() or ''}\n")
    return "\n".join(out)


def build(corpus_dir=None, index_dir=None, only=None, force=False):
    corpus_dir = corpus_dir or cfg.CORPUS_DIR
    index_dir = index_dir or cfg.INDEX_DIR
    t0 = time.time()
    cpath, vpath, mpath = index_paths(index_dir)
    os.makedirs(index_dir, exist_ok=True)
    emb = get_embedder()

    chunks: List[Chunk] = []
    docs_meta: Dict[str, dict] = {}
    for d in read_documents(corpus_dir):
        parsed = parse_document(d["text"], d["meta"])
        chunks += parsed
        docs_meta[parsed[0].doc_id if parsed else d["file"]] = {
            "file": d["file"], "sha256": d["sha256"], "version": parsed[0].doc_version if parsed else "",
            "source": next((l.split(":", 1)[1].strip() for l in d["text"].splitlines() if l.startswith("# source:")), ""),
            "n_chunks": len(parsed)}
    ids = [c.chunk_id for c in chunks]
    if len(ids) != len(set(ids)):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise SystemExit(f"duplicate chunk ids: {dup}")

    # reuse vectors for unchanged chunks
    old: Dict[str, np.ndarray] = {}
    if os.path.exists(cpath) and os.path.exists(vpath) and os.path.exists(mpath) and not force:
        old_meta = json.load(open(mpath))
        if old_meta.get("embedder_id") == emb.id:
            old_chunks = [json.loads(l) for l in open(cpath) if l.strip()]
            old_vecs = np.load(vpath)
            old = {c["hash"]: old_vecs[i] for i, c in enumerate(old_chunks)}
    todo = [i for i, c in enumerate(chunks) if force or c.hash not in old or (only and c.doc_id == only)]
    new_vecs = emb.encode([chunks[i].embed_text for i in todo]) if todo else np.zeros((0, emb.dim), "float32")
    vectors = np.zeros((len(chunks), emb.dim), dtype="float32")
    fresh = dict(zip(todo, new_vecs))
    for i, c in enumerate(chunks):
        vectors[i] = fresh[i] if i in fresh else old[c.hash]

    elapsed = max(time.time() - t0, 1e-6)
    meta = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "built_at_epoch": time.time(),
        "n_chunks": len(chunks), "n_embedded_this_run": len(todo), "n_reused": len(chunks) - len(todo),
        "embedder_id": emb.id, "chunking": cfg.CHUNKING, "chunk_headers": cfg.CHUNK_HEADERS,
        "documents": docs_meta, "elapsed_s": round(elapsed, 4),
        "throughput_chunks_per_s": round(len(chunks) / elapsed, 2),
    }
    for path, writer in ((cpath, lambda f: [f.write(json.dumps(c.to_dict()) + "\n") for c in chunks]),):
        with open(path + ".tmp", "w") as f:
            writer(f)
        os.replace(path + ".tmp", path)
    np.save(vpath + ".tmp.npy", vectors)
    os.replace(vpath + ".tmp.npy", vpath)
    with open(mpath + ".tmp", "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(mpath + ".tmp", mpath)  # meta is written last: its presence means the index is complete
    log(logger, "index built", **{k: meta[k] for k in ("n_chunks", "n_embedded_this_run", "n_reused", "embedder_id", "elapsed_s")})
    return meta


def main(argv=None):
    setup_logging()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus"), ap.add_argument("--index"), ap.add_argument("--only")
    ap.add_argument("--force", action="store_true"), ap.add_argument("--dump", action="store_true")
    ap.add_argument("--pdf-to-text")
    a = ap.parse_args(argv)
    if a.pdf_to_text:
        out = os.path.splitext(a.pdf_to_text)[0] + ".draft.txt"
        open(out, "w").write(pdf_to_text(a.pdf_to_text))
        print(f"wrote {out} - now add the header and [CLAUSE ...] markers (see corpus/README.md)")
        return 0
    if a.dump:
        for d in read_documents(a.corpus or cfg.CORPUS_DIR):
            for c in parse_document(d["text"], d["meta"]):
                print(f"{c.chunk_id:14} {c.regulator:5} {c.kind:16} refs={c.refs}  {c.heading}")
        return 0
    meta = build(a.corpus, a.index, a.only, a.force)
    print(json.dumps({k: meta[k] for k in ("built_at", "n_chunks", "n_embedded_this_run", "n_reused", "embedder_id", "throughput_chunks_per_s")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
