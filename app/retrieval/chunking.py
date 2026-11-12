"""Clause-aware chunking (L1).

The corpus is difficult because the clause that decides a case is rarely the one the
complaint resembles. So chunks are whole clauses (never cut mid-provision), each chunk
keeps its document, regulator, heading and `kind` (scope / maintainability / servicing /
general) as metadata, and the text that is *embedded* is prefixed with the document title
and clause heading (CHUNK_HEADERS=1). Cross references ("See clause X") are extracted so
the agent can follow them. The alternative, CHUNKING=fixed, slices by word count and is
kept only as an experiment.
"""
import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List

from ..config import cfg

CLAUSE_RE = re.compile(r"^\[CLAUSE\s+([^\s|\]]+)\s*\|\s*([^|\]]+?)\s*(?:\|\s*kind:\s*(\w+)\s*)?(?:\|\s*refs:\s*([^\]]+?)\s*)?\]\s*$")
HEADER_RE = re.compile(r"^#\s*(doc_id|regulator|title|version|source)\s*:\s*(.+?)\s*$")
# In-text references such as "under clause 10", "clause 3(1)(c)", "See clause 4.2", "regulation 21".
REF_RE = re.compile(r"\b(?:clauses?|regulations?)\s+((?:\d+[A-Z]?)(?:\.\d+)*(?:\(\w+\))*)", re.I)
KINDS = {"scope", "maintainability", "servicing", "general"}


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    regulator: str
    doc_title: str
    doc_version: str
    clause_id: str
    heading: str
    kind: str
    text: str
    refs: List[str] = field(default_factory=list)
    embed_text: str = ""
    hash: str = ""

    def to_dict(self):
        return asdict(self)


def _finish(c: Chunk) -> Chunk:
    c.text = c.text.strip()
    header = f"{c.doc_title} | {c.heading}\n" if cfg.CHUNK_HEADERS else ""
    c.embed_text = header + c.text
    c.hash = hashlib.sha256((c.chunk_id + "\x00" + c.embed_text).encode()).hexdigest()[:20]
    return c


def parse_document(text: str, default_meta: Dict[str, str] | None = None) -> List[Chunk]:
    meta = {"doc_id": "", "regulator": "", "title": "", "version": "", "source": ""}
    meta.update(default_meta or {})
    clauses = []  # (clause_id, heading, kind, [lines], curated refs)
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        m = HEADER_RE.match(line)
        if m and current is None:
            meta[m.group(1)] = m.group(2)
            continue
        c = CLAUSE_RE.match(line)
        if c:
            curated = [r.strip() for r in (c.group(4) or "").split(",") if r.strip()]
            current = [c.group(1), c.group(2).strip(), (c.group(3) or "general").lower(), [], curated]
            clauses.append(current)
            continue
        if current is not None and not line.startswith("#"):
            current[3].append(line)
    if not meta["doc_id"]:
        raise ValueError("document has no '# doc_id:' header")
    chunks: List[Chunk] = []
    known = [c[0] for c in clauses]
    for clause_id, heading, kind, lines, curated in clauses:
        body = " ".join(l.strip() for l in lines if l.strip())
        if not body:
            continue
        if kind not in KINDS:
            kind = "general"
        refs = [r if "#" in r else f"{meta['doc_id']}#{r}" for r in curated]
        for m in REF_RE.finditer(body):
            target = resolve_ref(m.group(1), known)
            if target and target != clause_id:
                refs.append(f"{meta['doc_id']}#{target}")
        chunks.append(_finish(Chunk(
            chunk_id=f"{meta['doc_id']}#{clause_id}", doc_id=meta["doc_id"], regulator=meta["regulator"],
            doc_title=meta["title"], doc_version=meta["version"], clause_id=clause_id, heading=heading,
            kind=kind, text=body, refs=sorted(set(refs)))))
    if cfg.CHUNKING == "fixed":
        chunks = _fixed(chunks, meta)
    return chunks


def resolve_ref(ref: str, known: List[str]) -> str | None:
    """Map an in-text reference ("10", "10(2)(a)", "3(1)(c)") to the most specific clause id that exists:
    an exact match, else the longest known id that starts with the reference, else the longest known id
    that the reference starts with (a reference to a sub-clause we did not index resolves to its parent)."""
    if ref in known:
        return ref
    longer = [k for k in known if k.startswith(ref) and (len(k) == len(ref) or not k[len(ref)].isdigit())]
    if longer:
        return min(longer, key=len)
    shorter = [k for k in known if ref.startswith(k) and not ref[len(k)].isdigit()]
    return max(shorter, key=len) if shorter else None


def _fixed(clause_chunks: List[Chunk], meta) -> List[Chunk]:
    """Experiment: ignore clause boundaries and cut by words. Loses clause-level citations."""
    words = []
    for c in clause_chunks:
        words += c.text.split()
    size, overlap = max(20, cfg.CHUNK_WORDS), min(cfg.CHUNK_OVERLAP, cfg.CHUNK_WORDS // 2)
    out, i, n = [], 0, 0
    while i < len(words):
        piece = " ".join(words[i:i + size])
        out.append(_finish(Chunk(
            chunk_id=f"{meta['doc_id']}#w{n}", doc_id=meta["doc_id"], regulator=meta["regulator"],
            doc_title=meta["title"], doc_version=meta["version"], clause_id=f"w{n}", heading=f"window {n}",
            kind="general", text=piece)))
        i += size - overlap
        n += 1
    return out
