"""Agent tools (A1). Retrieval is a tool the agent chooses to call, not a fixed stage.

All tools are read-only. Arguments are validated; unknown tools and bad arguments return an
error observation rather than raising, so a confused model cannot crash or escape the loop.
"""
from typing import Any, Dict, List, Optional

from ..config import cfg
from ..retrieval.index import VectorIndex

TOOL_SPECS = [
    {"name": "search_corpus",
     "description": "Semantic search over the regulatory corpus. Optional filters: regulator (SEBI|RBI), kind (scope|maintainability|servicing|general).",
     "args": {"query": "string", "regulator": "string?", "kind": "string?", "top_k": "int?"}},
    {"name": "get_clause",
     "description": "Fetch clauses by exact identifier, e.g. 'RBI-IOS#10(2)(a)'. Use it to follow 'See clause ...' cross references.",
     "args": {"ids": "list of string"}},
    {"name": "list_clauses",
     "description": "List clauses of one regulator filtered by kind; with a query they are ranked by relevance. Use kind=scope to see what a framework covers, maintainability for conditions that bar or delay a complaint, servicing for timelines.",
     "args": {"regulator": "string", "kind": "string?", "query": "string?", "limit": "int?"}},
    {"name": "get_complaint_fields",
     "description": "Return the structured fields of the request being processed (dates, entity name, prior complaint, court status).",
     "args": {}},
]
TOOL_NAMES = [t["name"] for t in TOOL_SPECS]
MAX_TEXT_TO_MODEL = 1200


class ToolError(Exception):
    pass


class ToolBox:
    def __init__(self, index: VectorIndex, fields: dict):
        self.index = index
        self.fields = fields
        self.retrieved: Dict[str, dict] = {}   # chunk_id -> full passage; the ground truth for verification

    def _pack(self, chunk, score: Optional[float] = None) -> dict:
        d = {"id": chunk.chunk_id, "doc_id": chunk.doc_id, "doc_title": chunk.doc_title, "regulator": chunk.regulator,
             "kind": chunk.kind, "heading": chunk.heading, "clause_id": chunk.clause_id, "text": chunk.text,
             "refs": list(chunk.refs), "score": None if score is None else round(float(score), 4)}
        self.retrieved[chunk.chunk_id] = d
        return d

    @staticmethod
    def _opt_str(args, key):
        v = args.get(key)
        if v is None or v == "":
            return None
        if not isinstance(v, str):
            raise ToolError(f"{key} must be a string")
        return v

    def search_corpus(self, args: dict) -> dict:
        q = args.get("query")
        if not isinstance(q, str) or not q.strip():
            raise ToolError("query must be a non-empty string")
        k = args.get("top_k", cfg.TOP_K)
        k = k if isinstance(k, int) and 1 <= k <= 10 else cfg.TOP_K
        hits = self.index.search(q[:2000], k, self._opt_str(args, "regulator"), self._opt_str(args, "kind"))
        return {"passages": [self._pack(c, s) for c, s in hits]}

    def get_clause(self, args: dict) -> dict:
        ids = args.get("ids") if args.get("ids") is not None else args.get("id")
        if isinstance(ids, str):
            ids = [ids]
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise ToolError("ids must be a non-empty list of clause identifiers")
        found, missing = [], []
        for cid in ids[:6]:
            ch = self.index.get(cid)
            (found if ch else missing).append(self._pack(ch) if ch else cid)
        return {"passages": found, "not_found": missing}

    def list_clauses(self, args: dict) -> dict:
        reg = self._opt_str(args, "regulator")
        if not reg:
            raise ToolError("regulator is required")
        kind = self._opt_str(args, "kind")
        limit = args.get("limit", 4)
        limit = limit if isinstance(limit, int) and 1 <= limit <= 8 else 4
        query = self._opt_str(args, "query")
        if query:
            return {"passages": [self._pack(c, s) for c, s in self.index.search(query, limit, reg, kind)]}
        return {"passages": [self._pack(c) for c in self.index.list_clauses(reg, kind)[:limit]]}

    def get_complaint_fields(self, args: dict) -> dict:
        return {"fields": dict(self.fields)}

    def execute(self, tool: str, args: Any) -> dict:
        if tool not in TOOL_NAMES:
            return {"error": f"unknown tool {tool!r}; available: {TOOL_NAMES}"}
        if not isinstance(args, dict):
            return {"error": "args must be an object"}
        try:
            return getattr(self, tool)(args)
        except ToolError as exc:
            return {"error": str(exc)}
