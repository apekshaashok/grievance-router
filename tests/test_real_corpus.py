"""Behaviour that depends on the real corpus (SEBI SCORES circular 2023/156, SEBI MF Regulations, RBI IOS 2021)."""
import json

from app.agents.graph import run_agent
from app.agents.timeline import days_stated, word_to_int
from app.agents.verifier import verify_determination
from app.guardrails import filter_output
from app.llm.base import LLMResult
from app.llm.resilience import CircuitBreaker, ResilientLLM
from app.retrieval.chunking import resolve_ref
from .conftest import GOOD


def test_section_4_1_case_goes_to_rbi_and_cites_the_sebi_exclusion():
    r = run_agent(GOOD)
    d = r["determination"]
    assert d["route"] == "refer-to-other-regulator" and d["regulator"] == "RBI"
    cites = {b["cite"] for b in d["basis"]}
    assert "SEBI-SCORES#AII-5.3" in cites                 # the provision that modifies SEBI's framework
    assert {"RBI-IOS#3(1)(e)", "RBI-IOS#9"} <= cites      # the framework that covers banks
    assert all(c["id"] in r["_retrieved"] for c in r["citations"])   # every citation resolves to retrieved text


def test_timelines_written_in_words_and_digits():
    assert word_to_int("twenty-one") == 21 and word_to_int("21") == 21
    assert days_stated("shall redress investor grievances promptly but not later than twenty-one calendar days from") == {21}
    assert days_stated("shall resolve the complaint within 21 calendar days of receipt") == {21}


def test_in_text_references_resolve_to_indexed_clauses():
    known = ["10(1)", "10(2)(a)", "10(2)(b)", "16", "8(2)"]
    assert resolve_ref("10", known) == "10(1)"
    assert resolve_ref("16(2)(c)", known) == "16"
    assert resolve_ref("8(2)", known) == "8(2)"
    assert resolve_ref("3(1)(c)", known) is None


def _passages(*ids):
    from app.retrieval.index import load_index
    idx = load_index()
    return {i: {"id": i, **{k: getattr(idx.get(i), k) for k in ("regulator", "kind", "text")}} for i in ids}


def test_referral_away_from_sebi_must_cite_the_sebi_exclusion():
    det = {"route": "refer-to-other-regulator", "regulator": "RBI", "domain": "out_of_jurisdiction", "entity_type": "bank",
           "summary": "Refer to the RBI Ombudsman.", "next_step": None, "timeline_days": None, "timeline_text": None,
           "basis": [{"claim": "Any customer aggrieved by an act or omission of a Regulated Entity resulting in deficiency in service may file a complaint under the Scheme", "cite": "RBI-IOS#9"}],
           "escalation": None}
    ret = _passages("RBI-IOS#9", "SEBI-SCORES#AII-5.3")
    issues = verify_determination(det, ret, GOOD)
    assert any("addressed to SEBI" in i for i in issues)
    det["basis"].append({"claim": "Complaints falling under the purview of other regulatory bodies such as Reserve Bank of India, (RBI)", "cite": "SEBI-SCORES#AII-5.3"})
    assert verify_determination(det, ret, GOOD) == []


def test_conditional_wording_is_not_a_disposal():
    ok = {"summary": "Refer to RBI.", "next_step": "Approach the Ombudsman if it is rejected or there is no reply.", "basis": []}
    bad = {"summary": "Your complaint has been resolved.", "next_step": None, "basis": []}
    assert filter_output(ok) == [] and filter_output(bad)


def test_repeated_tool_call_is_not_rerun_and_run_still_ends():
    class Looper:
        name = "looper"
        def complete(self, system, user, step, timeout=None):
            return LLMResult(json.dumps({"action": "tool", "tool": "search_corpus", "args": {"query": "bank"}}), 1, 1, "looper")
    r = run_agent(GOOD, llm=ResilientLLM(Looper(), None, CircuitBreaker(3, 5)))
    assert r["determination"]["escalation"]["reason"] == "limits_exceeded"
    dup = [e for e in r["_trace"] if e["type"] == "tool" and (e.get("error") or "").startswith("duplicate call")]
    assert dup, "duplicate calls must be answered with an observation, not re-executed"
