"""What counts as evidence from the complaint itself, and how a claim is checked against a passage."""
import re
from typing import Dict, List

from ..retrieval.embedder import tokens

SUPPORT_THRESHOLD = 0.7
_NUM = re.compile(r"\d+")


def complaint_evidence(fields: dict) -> str:
    """Complaint text plus plain statements derived from its structured fields."""
    parts = [fields.get("complaint_text", "")]
    if fields.get("pending_in_court"):
        parts.append("The complaint states the matter is pending before a court.")
    if fields.get("prior_complaint_to_entity"):
        parts.append("The investor has already complained to the entity.")
    else:
        parts.append("The investor has not yet complained to the entity.")
    try:
        from datetime import date
        t, c = fields.get("transaction_date"), fields.get("complaint_date")
        if t and c and (date.fromisoformat(str(c)) - date.fromisoformat(str(t))).days > 365:
            parts.append("The transaction date is more than one year before the complaint date.")
    except ValueError:
        pass
    if fields.get("entity_name"):
        parts.append(f"The entity named is {fields['entity_name']}.")
    return " ".join(parts)


def support_score(claim: str, passage: str) -> float:
    """Share of the claim's content words found in the passage; 0 if any number in the claim is absent."""
    ct = set(tokens(claim))
    if not ct:
        return 1.0
    if not set(_NUM.findall(claim)) <= set(_NUM.findall(passage)):
        return 0.0
    return len(ct & set(tokens(passage))) / len(ct)


def check_claims(det: dict, passages: Dict[str, str], evidence: str) -> List[str]:
    """Return issues: citations that do not resolve, or claims the cited text does not support."""
    issues = []
    for c in det.get("basis", []):
        cite, claim = c.get("cite", ""), c.get("claim", "")
        if cite == "complaint":
            if support_score(claim, evidence) < SUPPORT_THRESHOLD:
                issues.append(f"claim not supported by the complaint: {claim[:80]!r}")
            continue
        if cite not in passages:
            issues.append(f"citation {cite} does not resolve to any retrieved passage")
            continue
        if support_score(claim, passages[cite]) < SUPPORT_THRESHOLD:
            issues.append(f"claim not supported by {cite}: {claim[:80]!r}")
    return issues
