"""LangGraph orchestration (A1, A2, A3, A5).

  START -> investigate <-> tools          Agent 1: Investigator (LLM chooses tools; bounded loop)
              |
              v
           verify  --fail (attempts left)--> investigate   Agent 2: Verifier/Router (separate prompt, no tools)
              |  \\--fail (out of attempts)--> escalate
              v
           finalize (output filter) -> END

Hand-off: the Investigator's proposed determination and the full set of passages it retrieved are
passed to the Verifier; the Verifier alone can release an answer. Any limit hit (steps, tool calls,
wall clock), provider outage that cannot be degraded around, or exhausted verification attempts
ends in an escalation, never in a confident answer.
"""
import concurrent.futures as cf
import copy
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from .. import guardrails, metrics as M, prompts
from ..config import cfg
from ..contracts import DOMAINS
from ..llm.base import ProviderUnavailable
from ..llm.resilience import ResilientLLM, shared_llm
from ..logging_setup import log
from ..retrieval.index import VectorIndex, load_index
from ..telemetry import span
from .evidence import complaint_evidence
from .tools import MAX_TEXT_TO_MODEL, TOOL_NAMES, ToolBox
from .verifier import verify_determination

logger = logging.getLogger("agent")
_TOOL_POOL = cf.ThreadPoolExecutor(max_workers=32, thread_name_prefix="tool")


class State(TypedDict, total=False):
    complaint: dict
    attempt: int
    hints: dict
    obs: list
    passages: dict
    steps: int
    tool_calls: int
    pending: dict
    proposal: dict
    first_proposal: dict
    final: dict
    verification: dict
    issues_history: list
    escalate: dict
    next: str


@dataclass
class RunContext:
    run_id: str
    correlation_id: str
    llm: ResilientLLM
    toolbox: ToolBox
    prompt_version: str
    fault: Optional[str] = None
    heartbeat: Optional[Callable[[], None]] = None
    started: float = field(default_factory=time.time)
    trace: List[dict] = field(default_factory=list)
    usage: Dict[str, dict] = field(default_factory=dict)
    degraded: bool = False

    # ---- bookkeeping ----
    def event(self, type_: str, **kw):
        self.trace.append({"seq": len(self.trace) + 1, "t": round(time.time() - self.started, 4),
                           "ts": time.time(), "type": type_, **kw})
        if self.heartbeat:
            self.heartbeat()

    def over_deadline(self) -> bool:
        return time.time() - self.started > cfg.RUN_TIMEOUT_S

    def _account(self, step, res, dt):
        cost = res.prompt_tokens / 1000 * cfg.PRICE_PROMPT_PER_1K + res.completion_tokens / 1000 * cfg.PRICE_COMPLETION_PER_1K
        u = self.usage.setdefault(step, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0})
        u["calls"] += 1
        u["prompt_tokens"] += res.prompt_tokens
        u["completion_tokens"] += res.completion_tokens
        u["cost_usd"] += cost
        u["latency_s"] += dt
        M.TOKENS.labels("prompt", step).inc(res.prompt_tokens)
        M.TOKENS.labels("completion", step).inc(res.completion_tokens)
        M.COST.labels(step).inc(cost)
        M.STEP_LAT.labels(step).observe(dt)
        self.degraded = self.degraded or res.degraded
        return cost

    # ---- model call: prompt version, parse, format retry, defined fallback ----
    def call_llm(self, role: str, step: str, payload: dict, parse: Callable[[Any], Any]):
        system, sha = prompts.load(role, self.prompt_version)
        for _ in range(2):
            t0 = time.time()
            with span(f"llm.{step}", run_id=self.run_id, prompt_version=self.prompt_version):
                res = self.llm.complete(system, json.dumps(payload, default=str), step,
                                        deadline=self.started + cfg.RUN_TIMEOUT_S)
            dt = time.time() - t0
            cost = self._account(step, res, dt)
            self.event("llm", step=step, role=role, prompt_version=self.prompt_version, prompt_sha=sha, provider=res.provider,
                       model=res.model, degraded=res.degraded, prompt_tokens=res.prompt_tokens,
                       completion_tokens=res.completion_tokens, cost_usd=round(cost, 8), latency_s=round(dt, 4),
                       output=res.text[:1500])
            try:
                return parse(extract_json(res.text))
            except (ValueError, ValidationError) as exc:
                M.RETRIES.labels("format").inc()
                log(logger, "model output rejected", level="warning", step=step, error=str(exc)[:200])
                payload = {**payload, "format_error": str(exc)[:300]}
        t0 = time.time()
        res = self.llm.fallback_complete(system, json.dumps(payload, default=str), step)  # last resort for this step
        cost = self._account(step, res, time.time() - t0)
        self.event("llm", step=step, role=role, prompt_version=self.prompt_version, prompt_sha=sha, provider=res.provider,
                   degraded=True, prompt_tokens=res.prompt_tokens, completion_tokens=res.completion_tokens,
                   cost_usd=round(cost, 8), latency_s=round(time.time() - t0, 4), output=res.text[:1500], note="format fallback")
        return parse(extract_json(res.text))

    # ---- tool call: timeout + bounded retry with exponential backoff ----
    def run_tool(self, tool: str, args: dict) -> dict:
        last = "unknown error"
        for i in range(3):
            fut = _TOOL_POOL.submit(self.toolbox.execute, tool, args)
            try:
                return fut.result(timeout=cfg.TOOL_TIMEOUT_S)
            except cf.TimeoutError:
                last = f"tool timed out after {cfg.TOOL_TIMEOUT_S}s"
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
            M.RETRIES.labels("tool").inc()
            time.sleep(0.2 * (2 ** i))
        return {"error": last}


def extract_json(text: str) -> Any:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b < a:
        raise ValueError("no JSON object in model output")
    try:
        return json.loads(t[a:b + 1])
    except json.JSONDecodeError as exc:
        repaired = _close_brackets(t[a:])      # small models often drop the final "}" (INC-010)
        if repaired is not None:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
        raise ValueError(f"invalid JSON: {exc}") from exc


def _close_brackets(t: str) -> Optional[str]:
    """Append the closing brackets a truncated-by-one JSON text is missing. Only closes; never rewrites content."""
    stack, in_str, esc = [], False, False
    for ch in t:
        if in_str:
            esc, in_str = (False, in_str) if esc else (ch == "\\", ch != '"')
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack.pop() != ch:
                return None
    if in_str or not stack or len(stack) > 3:
        return None
    return t.rstrip() + "".join(reversed(stack))


def parse_action(obj: Any) -> dict:
    if not isinstance(obj, dict):
        raise ValueError("reply must be a JSON object")
    kind = obj.get("action")
    if kind == "tool":
        if obj.get("tool") not in TOOL_NAMES:
            raise ValueError(f"unknown tool {obj.get('tool')!r}; use one of {TOOL_NAMES}")
        args = obj.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError("args must be an object")
        return {"action": "tool", "tool": obj["tool"], "args": args}
    if kind == "finish":
        if not isinstance(obj.get("determination"), dict):
            raise ValueError("finish requires a determination object")
        return {"action": "finish", "determination": obj["determination"]}
    raise ValueError('"action" must be exactly "tool" or "finish". A route such as "refer-to-other-regulator" goes in '
                     'determination.route, e.g. {"action":"finish","determination":{"route":"...",...}}')


def parse_verdict(obj: Any) -> dict:
    if not isinstance(obj, dict) or not isinstance(obj.get("supported"), bool):
        raise ValueError('reply must be {"supported": bool, "issues": [...]}')
    issues = obj.get("issues") or []
    return {"supported": obj["supported"], "issues": [str(i) for i in issues][:10]}


def _inject_fault(det: dict, fault: str) -> dict:
    """Demo/test only (ENABLE_FAULT_INJECTION=1): corrupt the first proposal so verification has something to catch."""
    det = copy.deepcopy(det)
    if det.get("route") == "escalate-to-human" and fault != "bad_citation":
        return det
    if fault == "bad_citation":
        det.setdefault("basis", []).append({"claim": "Complaints of this kind must be resolved within 3 days.", "cite": "SEBI-SCORES#AI-9.9"})
    elif fault == "wrong_timeline":
        det["timeline_days"] = 3
    elif fault == "hallucinated_provision":
        cite = (det.get("basis") or [{"cite": "complaint"}])[0]["cite"]
        det.setdefault("basis", []).append({"claim": "Clause 7.4 requires the entity to pay compensation of ten percent of the invested amount.", "cite": cite})
    return det


def system_escalation(reason: str, detail: str) -> dict:
    return {"route": "escalate-to-human", "regulator": None, "domain": "unknown", "entity_type": None,
            "timeline_days": None, "timeline_text": None,
            "summary": f"Escalated to a human grievance officer ({reason.replace('_', ' ')}).",
            "next_step": "A grievance officer should review this complaint.", "basis": [],
            "escalation": {"reason": reason, "detail": detail[:480]}}


# ------------------------------- graph nodes -------------------------------
def _ctx(config) -> RunContext:
    return config["configurable"]["ctx"]


def n_investigate(state: State, config) -> dict:
    ctx = _ctx(config)
    if state["steps"] >= cfg.MAX_STEPS:
        return {"next": "escalate", "escalate": {"reason": "limits_exceeded", "detail": f"step limit {cfg.MAX_STEPS} reached"}}
    if ctx.over_deadline():
        return {"next": "escalate", "escalate": {"reason": "limits_exceeded", "detail": f"wall-clock limit {cfg.RUN_TIMEOUT_S}s reached"}}
    payload = {"step": "investigate", "complaint": state["complaint"], "observations": state["obs"],
               "passages": state["passages"],
               "attempt": state["attempt"], "hints": state["hints"],
               "limits": {"max_steps": cfg.MAX_STEPS, "steps_used": state["steps"], "max_tool_calls": cfg.MAX_TOOL_CALLS,
                          "tool_calls_used": state["tool_calls"]}}
    left = min(cfg.MAX_STEPS - state["steps"], cfg.MAX_TOOL_CALLS - state["tool_calls"] + 1)
    if left <= 2:   # tell the model before the bound is hit, so it finishes (or escalates) rather than being cut off
        payload["instruction"] = ("Your step budget is almost used. Reply now with action finish: route the complaint if the "
                                  "passages support it, otherwise escalate-to-human with reason insufficient_evidence.")
    try:
        action = ctx.call_llm("investigator", "investigate", payload, parse_action)
    except ProviderUnavailable as exc:
        return {"next": "escalate", "escalate": {"reason": "provider_unavailable", "detail": str(exc)}}
    steps = state["steps"] + 1
    if action["action"] == "finish":
        det = action["determination"]
        if state["attempt"] == 1 and ctx.fault and cfg.ENABLE_FAULT_INJECTION:
            det = _inject_fault(det, ctx.fault)
            ctx.event("fault_injected", fault=ctx.fault)
        out = {"proposal": det, "steps": steps, "next": "verify"}
        if "first_proposal" not in state:
            out["first_proposal"] = det
        return out
    if state["tool_calls"] >= cfg.MAX_TOOL_CALLS:
        return {"steps": steps, "next": "escalate", "escalate": {"reason": "limits_exceeded", "detail": f"tool-call limit {cfg.MAX_TOOL_CALLS} reached"}}
    return {"pending": {"tool": action["tool"], "args": action["args"]}, "steps": steps, "next": "tools"}


def n_tools(state: State, config) -> dict:
    ctx = _ctx(config)
    p = state["pending"]
    t0 = time.time()
    if any(o["tool"] == p["tool"] and o["args"] == p["args"] for o in state["obs"]):
        # a repeated call returns nothing new; say so instead of re-running it (weak models loop otherwise)
        result = {"error": "duplicate call: you already made this exact call and its passages are in `passages`. "
                           "Call a different tool, use different arguments, or finish."}
        M.RETRIES.labels("duplicate_tool_call").inc()
    else:
        with span(f"tool.{p['tool']}", run_id=ctx.run_id):
            result = ctx.run_tool(p["tool"], p["args"])
    dt = time.time() - t0
    M.STEP_LAT.labels(f"tool_{p['tool']}").observe(dt)
    ctx.event("tool", tool=p["tool"], args=p["args"], latency_s=round(dt, 5), error=result.get("error"),
              passages=[{"id": x["id"], "regulator": x["regulator"], "kind": x["kind"], "score": x.get("score"), "text": x["text"][:300]}
                        for x in result.get("passages", [])], not_found=result.get("not_found"))
    passages = dict(state["passages"])
    for x in result.get("passages", []):
        passages[x["id"]] = {**x, "text": x["text"][:MAX_TEXT_TO_MODEL]}   # each passage is sent to the model once
    entry = {"tool": p["tool"], "args": p["args"], "result_ids": [x["id"] for x in result.get("passages", [])]}
    if result.get("error"):
        entry["error"] = result["error"]
    if result.get("not_found"):
        entry["not_found"] = result["not_found"]
    if "fields" in result:
        entry["fields"] = result["fields"]
    obs = state["obs"] + [entry]
    return {"obs": obs, "passages": passages, "tool_calls": state["tool_calls"] + 1, "next": "investigate"}


def n_verify(state: State, config) -> dict:
    ctx = _ctx(config)
    det, attempt, fields = state["proposal"], state["attempt"], state["complaint"]
    if (det.get("regulator") or "").upper() == "RBI" and det.get("domain") not in DOMAINS:
        # for an RBI route the domain label is out_of_jurisdiction by definition; repair the label, record that we did
        ctx.event("normalized", field="domain", was=det.get("domain"), now="out_of_jurisdiction")
        det = {**det, "domain": "out_of_jurisdiction"}
    if det.get("route") != "escalate-to-human" and det.get("escalation") is not None:
        ctx.event("normalized", field="escalation", was=det.get("escalation"), now=None)
        det = {**det, "escalation": None}
    t0 = time.time()
    with span("verify", run_id=ctx.run_id, attempt=attempt):
        issues = verify_determination(det, ctx.toolbox.retrieved, fields)
        if not issues and cfg.VERIFY_WITH_LLM:
            cited = {c.get("cite") for c in det.get("basis", [])}
            payload = {"step": "verify", "determination": det,
                       "passages": {k: v["text"][:1500] for k, v in ctx.toolbox.retrieved.items() if k in cited},
                       "complaint_evidence": complaint_evidence(fields)}
            try:
                verdict = ctx.call_llm("verifier", "verify", payload, parse_verdict)
                if not verdict["supported"]:
                    issues += verdict["issues"] or ["verifier judged the answer unsupported"]
            except ProviderUnavailable:
                ctx.event("verify_judge_skipped", reason="provider unavailable; deterministic checks only")
                ctx.degraded = True
    dt = time.time() - t0
    M.STEP_LAT.labels("verify").observe(dt)
    ok = not issues
    M.VERIFY.labels("pass" if ok else "fail").inc()
    ctx.event("verify", attempt=attempt, passed=ok, issues=issues, latency_s=round(dt, 4), route=det.get("route"))
    hist = state.get("issues_history", []) + ([issues] if issues else [])
    if ok:
        return {"final": det, "verification": {"passed": True, "attempts": attempt}, "next": "finalize", "issues_history": hist}
    if attempt < cfg.MAX_VERIFY_ATTEMPTS:
        M.RETRIES.labels("verify").inc()
        return {"attempt": attempt + 1, "issues_history": hist, "next": "investigate",
                "hints": {"verification_issues": issues,
                          "instruction": "Your previous answer failed verification. Use different searches, read every cross-referenced clause, and cite only retrieved text."}}
    return {"issues_history": hist, "next": "escalate",
            "escalate": {"reason": "verification_failed", "detail": "; ".join(issues)[:480]}}


def n_escalate(state: State, config) -> dict:
    ctx = _ctx(config)
    esc = state["escalate"]
    ctx.event("escalate", reason=esc["reason"], detail=esc["detail"])
    return {"final": system_escalation(esc["reason"], esc["detail"]),
            "verification": {"passed": False, "attempts": state["attempt"], "note": "system escalation"}}


def n_finalize(state: State, config) -> dict:
    ctx = _ctx(config)
    det = state["final"]
    problems = guardrails.filter_output(det)
    if problems:
        ctx.event("output_filter", blocked=True, problems=problems)
        det = system_escalation("output_filter_blocked", "; ".join(problems))
    return {"final": det}


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        g = StateGraph(State)
        g.add_node("investigate", n_investigate)
        g.add_node("tools", n_tools)
        g.add_node("verify", n_verify)
        g.add_node("escalate", n_escalate)
        g.add_node("finalize", n_finalize)
        g.set_entry_point("investigate")
        g.add_conditional_edges("investigate", lambda s: s["next"], {"tools": "tools", "verify": "verify", "escalate": "escalate"})
        g.add_edge("tools", "investigate")
        g.add_conditional_edges("verify", lambda s: s["next"], {"finalize": "finalize", "investigate": "investigate", "escalate": "escalate"})
        g.add_edge("escalate", "finalize")
        g.add_edge("finalize", END)
        _graph = g.compile()
    return _graph


def run_agent(fields: dict, *, run_id: Optional[str] = None, correlation_id: Optional[str] = None,
              fault: Optional[str] = None, llm: Optional[ResilientLLM] = None, index: Optional[VectorIndex] = None,
              heartbeat: Optional[Callable[[], None]] = None) -> dict:
    run_id = run_id or uuid.uuid4().hex[:12]
    index = index or load_index()
    safe, flags = guardrails.sanitize_fields(fields)
    ctx = RunContext(run_id=run_id, correlation_id=correlation_id or run_id, llm=llm or shared_llm(),
                     toolbox=ToolBox(index, safe), prompt_version=cfg.PROMPT_VERSION, fault=fault, heartbeat=heartbeat)
    ctx.event("start", prompt_version=ctx.prompt_version, index_built_at=index.meta.get("built_at"),
              n_chunks=index.meta.get("n_chunks"), guardrail_flags=flags)
    state0: State = {"complaint": safe, "attempt": 1, "hints": {}, "obs": [], "passages": {}, "steps": 0, "tool_calls": 0, "issues_history": []}
    with span("agent.run", run_id=run_id, correlation_id=ctx.correlation_id):
        out = get_graph().invoke(state0, config={"configurable": {"ctx": ctx}, "recursion_limit": 100})
    final = out["final"]
    retrieved = ctx.toolbox.retrieved
    citations = []
    for c in final.get("basis", []):
        p = retrieved.get(c["cite"])
        if p and not any(x["id"] == p["id"] for x in citations):
            citations.append({k: p[k] for k in ("id", "regulator", "doc_id", "doc_title", "clause_id", "heading", "kind", "text")})
    escalated = final["route"] == "escalate-to-human"
    exec_s = time.time() - ctx.started
    total = {k: sum(u[k] for u in ctx.usage.values()) for k in ("calls", "prompt_tokens", "completion_tokens", "cost_usd", "latency_s")}
    ctx.event("end", route=final["route"], escalated=escalated, degraded=ctx.degraded, attempts=out["attempt"], exec_s=round(exec_s, 4))
    M.RUN_STEPS.observe(out["steps"])
    M.RUN_TOOLS.observe(out["tool_calls"])
    if escalated:
        M.ESCALATIONS.labels(final["escalation"]["reason"]).inc()
    if ctx.degraded:
        M.DEGRADED.inc()
    return {
        "run_id": run_id, "correlation_id": ctx.correlation_id, "outcome": "escalated" if escalated else "routed",
        "determination": final, "citations": citations, "verification": out.get("verification", {}),
        "prompt_version": ctx.prompt_version, "degraded": ctx.degraded, "attempts": out["attempt"],
        "steps": out["steps"], "tool_calls": out["tool_calls"], "exec_s": round(exec_s, 4),
        "usage": {"by_step": ctx.usage, "total": total}, "guardrails": flags,
        "first_proposal": out.get("first_proposal"), "retrieved_ids": sorted(retrieved), "issues_history": out.get("issues_history", []),
        "_retrieved": retrieved, "_trace": ctx.trace,
    }
