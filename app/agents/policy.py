"""Rule-based decision engine ("rules" provider).

It plays the part of the model when no provider is configured, when the provider is down (the defined
fallback), and behind the stub service used for load testing. It emits the same JSON actions a real model is
asked to emit, so everything around it (graph, tools, verifier, metrics) is exercised identically. It only ever
sees what a model would see: the complaint fields and the passages returned by the tools it chose to call.

Prompt variants steer it through a first line `STRATEGY: naive|agentic` so prompt versions can be compared
offline. With a real model the same prompt text is simply instructions.

The agentic policy follows the five steps of the brief (section 5):
  1. search with the complaint text (retrieves the provision that APPEARS to apply)
  2. ask whether another provision modifies it: read the maintainability / exclusion clauses of the framework the
     first search pointed at (for a bank product that SEBI's search surfaces, that is SCORES Annexure II 5.3)
  3. search again with a query derived from what was read: product + entity -> the framework that covers it
  4. the Verifier checks every citation against retrieved text (agents/verifier.py)
  5. on failure: retry with different searches (attempt 2 here), then escalate (graph.py)
"""
import re
from datetime import date
from typing import Dict, List, Optional

from .domain import SEBI_DOMAINS, classify
from .evidence import check_claims
from .timeline import timelines

SENT_SPLIT = re.compile(r"(?<=[.;:])\s+(?=[A-Z(\"'])")

LABEL = {"broker": "stock broker", "mutual_fund": "mutual fund / asset management company",
         "registrar": "registrar to an issue / share transfer agent", "listed_company": "listed company",
         "depository": "depository / depository participant"}
# words that a scope passage must contain to show the SEBI framework covers this class of entity
LABEL_TERMS = {"broker": ["stock broker"],
               "mutual_fund": ["mutual fund", "asset management company", "unit holder", "unitholder"],
               "registrar": ["registrar", "share transfer agent"],
               "listed_company": ["listed compan"],
               "depository": ["depository participant", "depositories"]}
# RBI scope: which definition covers the entity named in the complaint
RBI_ENTITY_TERMS = {"nbfc": ["non-banking financial company", "nbfc"],
                    "payment": ["system participant", "payment system"],
                    "bank": ["banking company", "co-operative bank"]}
OUT_OF_CORPUS_REGULATOR = {"insurance": "insurance regulatory", "pension": "pension fund regulatory"}


def _strategy(system: str) -> str:
    m = re.search(r"^STRATEGY:\s*(\w+)", system or "", re.M)
    return m.group(1).lower() if m else "agentic"


def decide(system: str, payload: dict) -> dict:
    step = payload.get("step")
    if step == "investigate":
        return _investigate(system, payload)
    if step == "verify":
        issues = check_claims(payload["determination"], payload["passages"], payload["complaint_evidence"])
        return {"supported": not issues, "issues": issues}
    raise ValueError(f"unknown step {step!r}")


# ---------- helpers over observations ----------
def _tool(name: str, args: dict) -> dict:
    return {"action": "tool", "tool": name, "args": args}


def _did(obs, tool, **match) -> bool:
    return any(o["tool"] == tool and all(o["args"].get(k) == v for k, v in match.items()) for o in obs)


def _did_first_search(obs) -> bool:
    return any(o["tool"] == "search_corpus" and not o["args"].get("regulator") for o in obs)


def _attempted_ids(obs) -> set:
    ids = set()
    for o in obs:
        if o["tool"] == "get_clause":
            v = o["args"].get("ids") or o["args"].get("id") or []
            ids |= set([v] if isinstance(v, str) else v)
    return ids


def _sentences(text: str) -> List[str]:
    """Sentences, split also at ';' and ':' so a list item can be cited alone. A short fragment ending in ':' (a table
    row label such as "Stock Brokers:") stays joined to what follows, otherwise the claim would be the label alone."""
    out: List[str] = []
    for part in (p.strip() for p in SENT_SPLIT.split(text.strip())):
        if not part:
            continue
        if out and out[-1].endswith(":") and len(out[-1]) < 40:
            out[-1] = out[-1] + " " + part
        else:
            out.append(part)
    return out


def _best_sentence(text: str, terms: List[str]) -> str:
    sents = _sentences(text)
    low_terms = [t.lower() for t in terms]
    scored = sorted(sents, key=lambda s: -sum(t in s.lower() for t in low_terms))
    return (scored[0] if scored else text)[:600]


def _claim(p: dict, terms: List[str]) -> dict:
    return {"claim": _best_sentence(p["text"], terms), "cite": p["id"]}


def _complaint_claim(text: str, terms: List[str]) -> dict:
    return {"claim": _best_sentence(text, terms), "cite": "complaint"}


def _hits(p: dict, terms: List[str]) -> int:
    low = p["text"].lower()
    return sum(t.lower() in low for t in terms)


def _pick(passages: Dict[str, dict], reg: str, kind: str, terms: List[str], n: int = 1, require: bool = True) -> List[dict]:
    cands = [p for p in passages.values() if p["regulator"].upper() == reg and p["kind"] == kind]
    ranked = sorted(cands, key=lambda p: (-_hits(p, terms), len(p["text"]), p["id"]))
    good = [p for p in ranked if _hits(p, terms)]
    return (good if require else (good or ranked))[:n]


def _first_search_regs(obs, passages) -> List[str]:
    first = next((o for o in obs if o["tool"] == "search_corpus" and not o["args"].get("regulator")), None)
    regs = []
    for pid in (first or {}).get("result_ids", [])[:3]:
        r = passages.get(pid, {}).get("regulator", "").upper()
        if r and r not in regs:
            regs.append(r)
    return regs


def _regs_needed(cls: dict) -> List[str]:
    if cls["split"]:
        return ["SEBI", "RBI"]
    if cls["domain"] in SEBI_DOMAINS:
        return ["SEBI"]
    if cls["domain"] == "out_of_jurisdiction":
        return ["RBI"]
    return []


def _derived_query(cls: dict, reg: str) -> str:
    """A query built from what was identified (product and entity), not from the complaint's wording."""
    if reg == "RBI":
        bank = cls["hits"].get("bank", [])
        entity = "non-banking financial company" if "nbfc" in bank else "bank regulated entity"
        return f"{entity} deficiency in service customer complaint ombudsman " + " ".join(bank[:3])
    label = LABEL.get(cls["domain"], "registered intermediary")
    return f"{label} investor grievance complaint SCORES entities covered " + " ".join(cls["product_terms"][:3])


def _modifier_query(cls: dict) -> str:
    extra = {"insurance": "Insurance Regulatory", "pension": "Pension Fund Regulatory"}.get(cls.get("out_of_corpus") or "", "")
    return f"complaints not dealt through SCORES purview of other regulatory bodies Reserve Bank of India {extra}".strip()


def _over_one_year(fields: dict) -> bool:
    try:
        t, c = fields.get("transaction_date"), fields.get("complaint_date")
        return bool(t and c and (date.fromisoformat(str(c)) - date.fromisoformat(str(t))).days > 365)
    except ValueError:
        return False


# ---------- the investigator policy ----------
def _investigate(system: str, payload: dict) -> dict:
    obs = payload.get("observations", [])
    attempt = int(payload.get("attempt", 1))
    strategy = "agentic" if attempt > 1 else _strategy(system)
    fields = payload["complaint"]
    text = fields.get("complaint_text", "")
    cls = classify(text, fields.get("entity_name"))
    passages = dict(payload.get("passages") or {})

    if strategy == "naive":
        if not _did(obs, "search_corpus"):
            return _tool("search_corpus", {"query": text})
        return {"action": "finish", "determination": _build_naive(cls, passages, obs)}

    if not _did(obs, "get_complaint_fields"):
        return _tool("get_complaint_fields", {})
    if not _did_first_search(obs):                                   # step 1: what appears to apply
        return _tool("search_corpus", {"query": text})
    regs = _regs_needed(cls)
    # step 2: does another provision modify the framework the first search pointed at? A framework lists what it
    # covers, not what it excludes - the exclusions sit in its maintainability clauses.
    pointed = _first_search_regs(obs, passages)
    low = text.lower()
    named = [r for r, words in (("SEBI", ("sebi", "scores")), ("RBI", ("rbi", "reserve bank"))) if any(w in low for w in words)]
    for reg in named + pointed + (["SEBI"] if cls["out_of_corpus"] or cls["market_loss"] else []):
        if reg not in regs and not _did(obs, "list_clauses", regulator=reg, kind="maintainability"):
            return _tool("list_clauses", {"regulator": reg, "kind": "maintainability", "query": _modifier_query(cls), "limit": 4})
    for reg in regs:      # step 3: a search derived from what was read, aimed at the framework that covers the entity
        if not _did(obs, "search_corpus", regulator=reg):
            return _tool("search_corpus", {"query": _derived_query(cls, reg), "regulator": reg})
    for reg in regs:      # what does that framework actually cover?
        if not _did(obs, "list_clauses", regulator=reg, kind="scope"):
            return _tool("list_clauses", {"regulator": reg, "kind": "scope", "query": _derived_query(cls, reg), "limit": 4})
    if not cls["split"]:  # conditions that bar or delay the complaint within that framework
        for reg in regs:
            if not _did(obs, "list_clauses", regulator=reg, kind="maintainability"):
                q = "complaint shall not lie pending before court prior written complaint to the entity limitation period one year"
                return _tool("list_clauses", {"regulator": reg, "kind": "maintainability", "query": q, "limit": 4})
    if regs == ["SEBI"] and not _did(obs, "list_clauses", regulator="SEBI", kind="servicing"):
        q = LABEL.get(cls["domain"], "") + " shall resolve redress complaint grievance within calendar days"
        return _tool("list_clauses", {"regulator": "SEBI", "kind": "servicing", "query": q, "limit": 4})
    # provisions the request never mentions: follow the cross references of the clauses that decide the case
    deciding = [p for p in passages.values() if p["regulator"].upper() in regs and p["kind"] in ("scope", "servicing")]
    missing = sorted({r for p in deciding for r in p.get("refs", []) if r not in passages} - _attempted_ids(obs))
    if missing and sum(o["tool"] == "get_clause" for o in obs) < 2:
        return _tool("get_clause", {"ids": missing[:6]})
    return {"action": "finish", "determination": _build_agentic(cls, fields, passages)}


def _esc(reason: str, detail: str, dom: str, basis: List[dict], summary: str) -> dict:
    return {"route": "escalate-to-human", "regulator": None, "domain": dom, "entity_type": None,
            "timeline_days": None, "timeline_text": None, "summary": summary,
            "next_step": "A grievance officer should review this complaint.", "basis": basis,
            "escalation": {"reason": reason, "detail": detail}}


def _not_maintainable(reg, dom, label, clause, terms, why, complaint_claim) -> dict:
    return {"route": "not-maintainable", "regulator": reg, "domain": dom, "entity_type": label,
            "timeline_days": None, "timeline_text": None,
            "summary": f"Not maintainable under the {reg} framework: {why}.",
            "next_step": "Grievance officer to confirm the facts with the investor before communicating the outcome.",
            "basis": [_claim(clause, terms), complaint_claim], "escalation": None}


def _build_agentic(cls: dict, fields: dict, passages: Dict[str, dict]) -> dict:
    text, dom = fields.get("complaint_text", ""), cls["domain"]
    terms = cls["product_terms"]
    if cls["out_of_corpus"]:
        label = cls["out_of_corpus"]
        basis = [_complaint_claim(text, terms)]
        kw = OUT_OF_CORPUS_REGULATOR.get(label)
        excl = _pick(passages, "SEBI", "maintainability", [kw]) if kw else []
        if excl:
            basis.insert(0, _claim(excl[0], [kw, "purview"]))
        return _esc("entity_out_of_corpus", f"The complaint concerns {label}; no framework in the indexed corpus covers it.",
                    "out_of_corpus", basis,
                    f"This complaint appears to concern {label}, which is outside the regulatory documents indexed. Escalating to a human.")
    if dom == "unknown":
        return _esc("insufficient_evidence", "The complaint does not identify a product or entity that can be matched to a framework.",
                    "unknown", [_complaint_claim(text, [])],
                    "The complaint is too vague to determine the entity or regulator. Escalating to a human.")
    if cls["split"]:
        basis = []
        for reg, tl in (("SEBI", LABEL_TERMS.get(dom, [])), ("RBI", ["bank", "system participant", "payment"])):
            picked = _pick(passages, reg, "scope", tl)
            if not picked:
                return _esc("insufficient_evidence", f"No {reg} scope clause could be retrieved.", dom, [_complaint_claim(text, terms)],
                            "Evidence for one of the two frameworks could not be retrieved. Escalating to a human.")
            basis.append(_claim(picked[0], tl))
        basis.append(_complaint_claim(text, terms))
        return _esc("split_regulators", "The complaint involves both a bank or payment system (RBI framework) and a SEBI-regulated entity.",
                    dom, basis, "This complaint splits across two regulators, so it cannot be routed to one. Escalating to a human.")

    reg = "RBI" if dom == "out_of_jurisdiction" else "SEBI"
    label = LABEL.get(dom, "bank or other RBI-regulated entity")
    if cls["market_loss"]:
        return _esc("insufficient_evidence",
                    "The complaint alleges a loss from market movement and no service deficiency; no indexed provision decides "
                    "whether such a complaint is maintainable.", dom, [_complaint_claim(text, ["market", "fell", "lost", "nav"])],
                    "No provision in the corpus addresses a loss caused by market movement. Escalating to a human.")
    if fields.get("pending_in_court"):
        mc = _pick(passages, reg, "maintainability", ["court", "tribunal"])
        if not mc:
            return _esc("insufficient_evidence", "No clause on matters pending before a court was retrieved.", dom,
                        [_complaint_claim(text, terms)], "Could not find the governing clause. Escalating to a human.")
        return _not_maintainable(reg, dom, label, mc[0], ["court", "pending"], "the same matter is stated to be pending before a court",
                                 {"claim": "The complaint states the matter is pending before a court.", "cite": "complaint"})
    if reg == "SEBI" and cls.get("unlisted"):
        mc = _pick(passages, "SEBI", "maintainability", ["unlisted", "delisted"])
        if mc:
            return _not_maintainable("SEBI", dom, "unlisted company", mc[0], ["unlisted"],
                                     "complaints against unlisted or delisted companies are not dealt through SCORES",
                                     _complaint_claim(text, ["unlisted", "delisted"]))
    if reg == "SEBI" and _over_one_year(fields):
        mc = _pick(passages, "SEBI", "maintainability", ["one year", "limitation"])
        if mc:
            return _not_maintainable("SEBI", dom, label, mc[0], ["one year"],
                                     "it is lodged more than one year after the cause of action",
                                     {"claim": "The transaction date is more than one year before the complaint date.", "cite": "complaint"})

    if reg == "RBI":
        bank_hits = cls["hits"].get("bank", [])
        kind_key = "nbfc" if "nbfc" in bank_hits else ("payment" if set(bank_hits) & {"upi", "neft", "imps"} else "bank")
        ent = _pick(passages, "RBI", "scope", RBI_ENTITY_TERMS[kind_key])
        cover = _pick(passages, "RBI", "scope", ["deficiency in service", "aggrieved"])
        if not ent or not cover:
            return _esc("insufficient_evidence", "Could not retrieve the RBI clauses that define the entity and the grounds of complaint.",
                        dom, [_complaint_claim(text, terms)], "Could not retrieve the RBI framework. Escalating to a human.")
        basis = [_claim(ent[0], RBI_ENTITY_TERMS[kind_key])]
        reg_entity = passages.get("RBI-IOS#3(1)(j)")
        if reg_entity and reg_entity["id"] != ent[0]["id"]:
            basis.append(_claim(reg_entity, ["regulated entity", "bank"]))
        basis.append(_claim(cover[0], ["aggrieved", "deficiency in service"]))
        excl = _pick(passages, "SEBI", "maintainability", ["reserve bank of india"])
        if excl:      # the provision that modifies the SEBI framework the complaint invoked
            basis.append(_claim(excl[0], ["reserve bank", "purview"]))
        pre = _pick(passages, "RBI", "maintainability", ["written complaint", "30 days"])
        if pre:
            basis.append(_claim(pre[0], ["written complaint", "30 days"]))
        basis.append(_complaint_claim(text, terms))
        prior = fields.get("prior_complaint_to_entity")
        return {"route": "refer-to-other-regulator", "regulator": "RBI", "domain": "out_of_jurisdiction",
                "entity_type": {"nbfc": "non-banking financial company", "payment": "bank / payment system participant"}.get(kind_key, "bank"),
                "timeline_days": None, "timeline_text": None,
                "summary": ("This complaint concerns a product of an RBI-regulated entity, which falls under the Reserve Bank - "
                            "Integrated Ombudsman Scheme, 2021 and not under SEBI's SCORES framework. Refer it to the RBI Ombudsman."),
                "next_step": ("The investor should first make a written complaint to the regulated entity and may approach the RBI "
                              "Ombudsman (cms.rbi.org.in) if it is rejected or there is no reply within 30 days."
                              if not prior else "The investor has already complained to the regulated entity; the RBI Ombudsman route "
                              "(cms.rbi.org.in) is open if the reply was unsatisfactory or none came within 30 days."),
                "basis": basis, "escalation": None}

    # SEBI-regulated entity
    scope = _pick(passages, "SEBI", "scope", LABEL_TERMS.get(dom, []), n=2)
    tl = _timeline(passages, dom)
    if not scope or tl is None:
        return _esc("insufficient_evidence", "Could not retrieve both the scope clause and the redressal timeline for this entity type.",
                    dom, [_complaint_claim(text, terms)], "Evidence incomplete. Escalating to a human.")
    tl_chunk, days, sent = tl
    basis = [_claim(p, LABEL_TERMS.get(dom, [])) for p in scope]
    entities = passages.get("SEBI-SCORES#P1")
    if entities and entities["id"] not in {p["id"] for p in scope}:
        basis.append(_claim(entities, ["listed company", "registered intermediary"]))
    basis += [{"claim": sent, "cite": tl_chunk["id"]}, _complaint_claim(text, terms)]
    prior = fields.get("prior_complaint_to_entity")
    return {"route": "assign-to-entity", "regulator": "SEBI", "domain": dom, "entity_type": label,
            "timeline_days": days, "timeline_text": sent,
            "summary": f"Assign to the {label} for redressal within {days} calendar days under the SEBI framework.",
            "next_step": ("The entity should redress the complaint and submit its Action Taken Report within the stated period; "
                          "the grievance officer tracks it." if prior else
                          "The investor has not yet approached the entity; under SEBI's framework the grievance is first taken up "
                          "with the entity, which must redress it within the stated period."),
            "basis": basis, "escalation": None}


def _timeline(passages: Dict[str, dict], dom: str):
    """The redressal timeline for this entity type: an entity-specific servicing clause if one states a period,
    otherwise the clause that applies to all Entities on SCORES. Returns (passage, days, sentence) or None."""
    terms = LABEL_TERMS.get(dom, [])
    cands = []
    for p in passages.values():
        if p["regulator"].upper() != "SEBI" or p["kind"] != "servicing":
            continue
        for days, sent in timelines(p["text"]):
            low = sent.lower()
            if not re.search(r"redress|resolve", low):
                continue
            specific = _hits({"text": sent}, terms)
            general = "all entities" in low
            if specific or general:
                cands.append((-(specific * 2 + general), p["id"], p, days, sent))
    if not cands:
        return None
    _, _, p, days, sent = sorted(cands, key=lambda c: (c[0], c[1]))[0]
    return p, days, sent


def _build_naive(cls: dict, passages: Dict[str, dict], obs) -> dict:
    """Baseline: route by the regulator of the single best-matching passage and always answer."""
    first = next((o for o in obs if o["tool"] == "search_corpus"), None)
    ids = (first or {}).get("result_ids", [])
    top = passages.get(ids[0]) if ids else None
    if top is None:
        return _esc("insufficient_evidence", "Nothing retrieved.", "unknown", [{"claim": "No passage was retrieved.", "cite": "complaint"}], "Nothing found.")
    dom = cls["domain"] if cls["domain"] in SEBI_DOMAINS else "broker"
    if top["regulator"].upper() == "RBI":
        return {"route": "refer-to-other-regulator", "regulator": "RBI", "domain": "out_of_jurisdiction",
                "entity_type": "bank or other RBI-regulated entity", "timeline_days": None, "timeline_text": None,
                "summary": "Refer to the RBI Ombudsman.", "next_step": None,
                "basis": [_claim(top, cls["product_terms"])], "escalation": None}
    tl = _timeline(passages, dom)
    return {"route": "assign-to-entity", "regulator": "SEBI", "domain": dom, "entity_type": LABEL[dom],
            "timeline_days": tl[1] if tl else None, "timeline_text": tl[2] if tl else None,
            "summary": f"Assign to the {LABEL[dom]} under the SEBI framework.", "next_step": None,
            "basis": [_claim(top, cls["product_terms"])] + ([{"claim": tl[2], "cite": tl[0]["id"]}] if tl else []),
            "escalation": None}
