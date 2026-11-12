"""Guardrails (L4): prompt-injection defence, toxicity detection, output filtering.

Layers:
 1. detect + neutralise instruction-like text in every free-text field before any model sees it
 2. pass whatever remains only as JSON string values, with a system rule that they are untrusted data
 3. tools are an allow-list with validated arguments (see agents/tools.py); no tool can write or send
 4. output filter: the system may only emit a routing determination, never a disposal
"""
import re
from typing import Dict, List, Tuple

from .metrics import GUARD

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)",
    r"disregard\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier|your)\s+(instructions?|prompts?|rules?)",
    r"forget\s+(everything|all)\s+(above|before|you\s+were\s+told)",
    r"you\s+are\s+now\s+(a|an|the)\b",
    r"(reveal|show|print|repeat)\s+(me\s+)?(your|the)\s+(system\s+)?(prompt|instructions)",
    r"\bsystem\s*prompt\b",
    r"\bdeveloper\s+mode\b|\bjailbreak\b|\bDAN\s+mode\b",
    r"(approve|reject|close|dispose\s+of|resolve)\s+(this|the|my)\s+(complaint|case|claim)",
    r"\b(mark|set|route|classify|label)\s+(this|it|the\s+complaint)\s+(as|to)\b",
    r"override\s+(the\s+)?(routing|determination|policy|rules?)",
    r"</?\s*(system|assistant|tool|instructions?)\s*>",
    r"\[\s*(system|inst)\s*\]",
    r"\bact\s+as\s+(a|an|the|if)\b",
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]

# Deliberately small and mild; extend via a file mounted at runtime if needed.
TOXIC_TERMS = ["idiot", "idiots", "stupid", "moron", "morons", "scumbag", "scumbags", "bastard", "bastards",
               "crook", "crooks", "damn", "shut up", "useless fools"]
_TOX = re.compile(r"\b(" + "|".join(re.escape(t) for t in TOXIC_TERMS) + r")\b", re.I)

FORBIDDEN_OUTPUT = [
    r"\b(has|have|had|is|are|been)\s+(been\s+)?(resolved|disposed|closed|approved|rejected|dismissed|upheld)\b",
    r"\bcase\s+(is\s+)?closed\b",
    r"\b(refund|compensation|reimbursement)\s+(will\s+be|is|has\s+been|shall\s+be)\s+(paid|granted|awarded|issued|processed)\b",
    r"\bwe\s+(hereby\s+)?(approve|reject|dismiss|uphold|award|order)\b",
    r"\b(final|binding)\s+(decision|order|award)\b",
]
_FORB = [re.compile(p, re.I) for p in FORBIDDEN_OUTPUT]
# "if it is rejected", "unless the complaint has been resolved": conditions, not statements of disposal
_CONDITIONAL = re.compile(r"\b(if|unless|when|where|whether|once|until)\b[^.;:]*$", re.I)
_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")


def scan_text(text: str) -> Dict:
    """Return sanitised text plus what was found. Never raises."""
    if not text:
        return {"text": text or "", "injection": [], "toxic": []}
    injection: List[str] = []
    clean = text
    for rx in _INJ:
        for m in rx.finditer(clean):
            injection.append(m.group(0)[:60])
        clean = rx.sub("[removed: instruction-like text]", clean)
    toxic = sorted({m.group(0).lower() for m in _TOX.finditer(clean)})
    clean = _TOX.sub("[abusive]", clean)
    return {"text": clean, "injection": injection, "toxic": toxic}


def sanitize_fields(fields: dict) -> Tuple[dict, dict]:
    """Sanitise every free-text field. Returns (safe_fields, flags)."""
    safe = dict(fields)
    flags = {"injection_detected": False, "injection_snippets": [], "toxic_input": False, "fields": []}
    for key in ("complaint_text", "entity_name"):
        val = safe.get(key)
        if not isinstance(val, str):
            continue
        res = scan_text(val)
        safe[key] = res["text"]
        if res["injection"]:
            flags["injection_detected"] = True
            flags["injection_snippets"] += res["injection"]
            flags["fields"].append(key)
            GUARD.labels("prompt_injection").inc()
        if res["toxic"]:
            flags["toxic_input"] = True
            GUARD.labels("toxic_input").inc()
    return safe, flags


def filter_output(det: dict) -> List[str]:
    """Output filter. Returns a list of problems; empty means acceptable."""
    problems: List[str] = []
    free_text = [det.get("summary") or "", det.get("next_step") or "", det.get("timeline_text") or ""]
    esc = det.get("escalation") or {}
    free_text.append(esc.get("detail") or "")
    blob = " ".join(free_text)
    for rx in _FORB:
        if any(not _CONDITIONAL.search(blob[max(0, m.start() - 40):m.start()]) for m in rx.finditer(blob)):
            problems.append("output makes a disposal-type statement the system is not authorised to make")
            GUARD.labels("output_blocked").inc()
            break
    everything = blob + " " + " ".join(c.get("claim", "") for c in det.get("basis", []))
    if _PAN.search(everything):
        problems.append("output contains a PAN-like identifier")
    if _TOX.search(everything):
        problems.append("output contains abusive language")
        GUARD.labels("toxic_output").inc()
    return problems
