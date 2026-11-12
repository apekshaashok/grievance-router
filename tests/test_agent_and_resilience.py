import json
import time

import pytest

from app.agents.graph import run_agent
from app.llm.base import LLMResult, ProviderError
from app.llm.resilience import CircuitBreaker, ResilientLLM
from app.llm.rules import RulesProvider
from .conftest import GOOD


class Down:
    name = "down"
    calls = 0

    def complete(self, *a, **k):
        Down.calls += 1
        raise ProviderError("connection refused")


def test_eval_set_meets_bar():
    from eval.run_eval import main
    assert main(["--fail-under", "0.95", "--out", "/tmp/eval-out"]) == 0


def test_baseline_prompt_fails_first_attempt_but_verifier_rescues(monkeypatch):
    monkeypatch.setenv("PROMPT_VERSION", "v1")
    upi = {**GOOD, "complaint_text": "A UPI payment of 5000 rupees failed but the amount was debited from my account and was not returned for ten days."}
    r = run_agent(upi)
    assert r["attempts"] == 2 and r["determination"]["regulator"] == "RBI"


def test_circuit_breaker_opens_and_degrades(monkeypatch):
    monkeypatch.setenv("LLM_BACKOFF_BASE_S", "0.01")
    monkeypatch.setenv("BREAKER_FAILURES", "3")
    monkeypatch.setenv("BREAKER_COOLDOWN_S", "0.3")
    Down.calls = 0
    llm = ResilientLLM(Down(), RulesProvider(), CircuitBreaker(3, 0.3))
    r = run_agent(GOOD, llm=llm)
    assert llm.breaker.state == "open"
    assert Down.calls == 3                                   # breaker stopped further calls
    assert r["degraded"] is True and r["determination"]["regulator"] == "RBI"   # defined fallback, still verified
    time.sleep(0.35)
    assert llm.breaker.allow()                               # half-open trial after cooldown


def test_no_fallback_means_escalation_not_a_guess(monkeypatch):
    monkeypatch.setenv("LLM_BACKOFF_BASE_S", "0.01")
    llm = ResilientLLM(Down(), None, CircuitBreaker(3, 5))
    r = run_agent(GOOD, llm=llm)
    assert r["outcome"] == "escalated" and r["determination"]["escalation"]["reason"] == "provider_unavailable"


def test_loop_limits_force_escalation(monkeypatch):
    monkeypatch.setenv("MAX_STEPS", "3")
    r = run_agent(GOOD)
    assert r["determination"]["escalation"]["reason"] == "limits_exceeded"
    monkeypatch.setenv("MAX_STEPS", "10")
    monkeypatch.setenv("RUN_TIMEOUT_S", "0")
    assert run_agent(GOOD)["determination"]["escalation"]["reason"] == "limits_exceeded"


def test_unverifiable_answer_is_never_returned(monkeypatch):
    class Liar:
        name = "liar"
        def complete(self, system, user, step, timeout=None):
            if step == "verify":
                return LLMResult(json.dumps({"supported": True, "issues": []}), 1, 1, "liar")
            out = {"action": "finish", "determination": {"route": "assign-to-entity", "regulator": "SEBI", "domain": "broker",
                   "entity_type": "stock broker", "timeline_days": 2, "timeline_text": None, "summary": "Assign it.",
                   "next_step": None, "basis": [{"claim": "Brokers must redress within 2 days.", "cite": "SEBI-SCORES#AI-1.2"}], "escalation": None}}
            return LLMResult(json.dumps(out), 1, 1, "liar")
    r = run_agent(GOOD, llm=ResilientLLM(Liar(), None, CircuitBreaker(3, 5)))
    assert r["determination"]["route"] == "escalate-to-human"
    assert r["determination"]["escalation"]["reason"] == "verification_failed"


def test_output_filter_blocks_disposal_language():
    from app.guardrails import filter_output
    assert filter_output({"summary": "The complaint has been resolved and compensation will be paid.", "basis": []})
    assert not filter_output({"summary": "Refer to the RBI Ombudsman.", "basis": []})


def test_tool_arguments_are_validated():
    from app.agents.tools import ToolBox
    from app.retrieval.index import load_index
    tb = ToolBox(load_index(), {})
    assert "error" in tb.execute("delete_everything", {})
    assert "error" in tb.execute("search_corpus", {"query": 5})
    assert tb.execute("get_clause", {"ids": ["RBI-IOS#10(2)(a)", "RBI-IOS#9.9"]})["not_found"] == ["RBI-IOS#9.9"]


def test_reaper_requeues_stale_run(store):
    from app.worker import process_message
    store.create_run("r1", GOOD, "c", "cid", {})
    store.mark_running("r1")
    store.r.hset("run:r1", "heartbeat", time.time() - 999)
    assert store.requeue_stale(120) == ["requeued:r1"]
    process_message(store, store.pop(timeout=1))
    assert store.get_run("r1")["status"] == "completed"


def test_ingestion_is_idempotent(tmp_path):
    from app import ingest
    a = ingest.build(index_dir=str(tmp_path))
    b = ingest.build(index_dir=str(tmp_path))
    assert a["n_chunks"] == b["n_chunks"] and b["n_embedded_this_run"] == 0 and b["n_reused"] == a["n_chunks"]
