"""Verifier / router gate (A3). Deterministic checks first; they are the hard gate. An optional model
judge (VERIFY_WITH_LLM) can only add objections, never remove them."""
import re
from typing import Dict, List

from pydantic import ValidationError

from .. import guardrails
from ..contracts import Determination, format_errors
from .evidence import SUPPORT_THRESHOLD, check_claims, complaint_evidence, support_score
from .timeline import days_stated


OTHER_REGULATOR_NAMES = {"RBI": "reserve bank"}


def verify_determination(det: dict, retrieved: Dict[str, dict], fields: dict) -> List[str]:
    try:
        model = Determination.model_validate(det)
    except ValidationError as exc:
        return ["schema: " + "; ".join(f"{e['field']}: {e['message']}" for e in format_errors(exc))[:400]]
    passages = {k: v["text"] for k, v in retrieved.items()}
    issues = check_claims(det, passages, complaint_evidence(fields))
    cited = [retrieved[c.cite] for c in model.basis if c.cite in retrieved]
    reg = (model.regulator or "").upper()

    def cited_kind(kind, regulator=None):
        return [p for p in cited if p["kind"] == kind and (regulator is None or p["regulator"].upper() == regulator)]

    if model.route == "assign-to-entity":
        if model.timeline_days is None:
            issues.append("assign-to-entity requires timeline_days")
        else:
            ok = any(model.timeline_days in days_stated(p["text"]) for p in cited_kind("servicing"))
            if not ok:
                issues.append(f"timeline of {model.timeline_days} days is not stated in any cited servicing passage")
        if model.timeline_text:
            if not any(support_score(model.timeline_text, p["text"]) >= SUPPORT_THRESHOLD for p in cited_kind("servicing")):
                issues.append("timeline_text is not supported by a cited servicing passage")
        if not cited_kind("scope", reg):
            issues.append(f"no cited {reg} scope clause shows that the framework covers this entity")
    elif model.route == "refer-to-other-regulator":
        if not cited_kind("scope", reg):
            issues.append(f"no cited {reg} scope clause shows that the {reg} framework covers this entity")
        # The investor addressed the complaint to SEBI. Referring it elsewhere needs the provision that takes it out of
        # SEBI's framework: SEBI's own clauses list what they cover, so the exclusion has to be found and cited.
        named_sebi = re.search(r"\b(sebi|scores)\b", (fields.get("complaint_text") or "").lower())
        other = OTHER_REGULATOR_NAMES.get(reg)
        if named_sebi and reg != "SEBI" and other and not any(other in p["text"].lower() for p in cited_kind("maintainability", "SEBI")):
            issues.append(f"the complaint is addressed to SEBI: cite the SEBI maintainability clause that excludes matters within "
                          f"the {reg} framework from SEBI's (search SEBI maintainability clauses for {other!r})")
    elif model.route == "not-maintainable":
        if not cited_kind("maintainability", reg):
            issues.append("not-maintainable requires a cited maintainability clause")
    elif model.route == "escalate-to-human" and model.escalation and model.escalation.reason == "split_regulators":
        if len({p["regulator"].upper() for p in cited_kind("scope")}) < 2:
            issues.append("split_regulators must cite a scope clause from each of two regulators")
    issues += guardrails.filter_output(det)
    return issues
