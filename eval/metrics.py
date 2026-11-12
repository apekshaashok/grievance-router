"""Evaluation metrics (L3) - lexical, claim-level equivalents of the TruLens/RAGAS measures. No model is needed,
so they are deterministic, free and runnable in CI. How each is computed:

claim                 one entry of `basis`: a statement plus the passage id it cites ("complaint" = the complaint itself)
supported(claim, t)   >= 70% of the claim's content words occur in text t, and every number in the claim occurs in t
groundedness          share of claims supported by the passage THEY CITE (a citation that does not resolve counts as unsupported)
faithfulness          share of claims supported by ANY retrieved passage (or the complaint): can the claim be inferred from context
hallucination rate    share of responses with at least one claim that is supported by nothing retrieved (faithfulness < 1)
context relevance     share of the retrieved passages that belong to the scenario's gold set (precision); reported with
                      gold recall (share of gold passages that were retrieved). Escalation-by-design scenarios have no gold set.
"""
from typing import Dict, List, Optional

from app.agents.evidence import SUPPORT_THRESHOLD, complaint_evidence, support_score


def claim_metrics(det: Optional[dict], retrieved_text: Dict[str, str], fields: dict) -> dict:
    claims = (det or {}).get("basis", [])
    if not claims:
        return {"n_claims": 0, "groundedness": None, "faithfulness": None, "hallucinated": False}
    evidence = complaint_evidence(fields)
    corpus = list(retrieved_text.values()) + [evidence]
    grounded = faithful = 0
    for c in claims:
        src = evidence if c.get("cite") == "complaint" else retrieved_text.get(c.get("cite"))
        g = src is not None and support_score(c["claim"], src) >= SUPPORT_THRESHOLD
        f = g or any(support_score(c["claim"], t) >= SUPPORT_THRESHOLD for t in corpus)
        grounded += g
        faithful += f
    n = len(claims)
    return {"n_claims": n, "groundedness": grounded / n, "faithfulness": faithful / n, "hallucinated": faithful < n}


def retrieval_metrics(retrieved_ids: List[str], gold: List[str]) -> dict:
    if not gold:
        return {"context_relevance": None, "gold_recall": None}
    r, g = set(retrieved_ids), set(gold)
    return {"context_relevance": len(r & g) / len(r) if r else 0.0, "gold_recall": len(r & g) / len(g)}


def outcome_correct(det: Optional[dict], expected: dict) -> bool:
    if not det or det.get("route") != expected["route"]:
        return False
    if (det.get("regulator") or None) != (expected.get("regulator") or None):
        return False
    if expected.get("domain") and det.get("domain") != expected["domain"]:
        return False
    if expected.get("timeline_days") is not None and det.get("timeline_days") != expected["timeline_days"]:
        return False
    if expected.get("escalation_reason"):
        if (det.get("escalation") or {}).get("reason") != expected["escalation_reason"]:
            return False
    return True


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None
