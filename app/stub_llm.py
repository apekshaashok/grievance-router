"""Stub model service (D6). OpenAI-compatible /chat/completions that answers after a realistic delay.

It returns the rule engine's JSON action, so the agent loop runs to completion and a load test measures THIS system
(queue, workers, retrieval, verification, logging) rather than a provider's rate limits.
STUB_DELAY_MS sets the delay; POST /admin/fail?on=true makes it return 503 (demonstrates the circuit breaker).
"""
import asyncio
import json
import random

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .agents.policy import decide
from .config import cfg

app = FastAPI(title="stub-llm")
_state = {"fail": bool(cfg.STUB_FAIL)}


@app.get("/healthz")
def healthz():
    return {"ok": True, "failing": _state["fail"], "delay_ms": cfg.STUB_DELAY_MS}


@app.post("/admin/fail")
def admin_fail(on: bool = True):
    _state["fail"] = on
    return {"failing": on}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    if _state["fail"]:
        return JSONResponse({"error": "stub is set to fail"}, status_code=503)
    await asyncio.sleep(cfg.STUB_DELAY_MS / 1000 * random.uniform(0.8, 1.2))
    msgs = body["messages"]
    system, user = msgs[0]["content"], msgs[-1]["content"]
    text = json.dumps(decide(system, json.loads(user)))
    pt, ct = (len(system) + len(user)) // 4, len(text) // 4
    return {"id": "stub", "object": "chat.completion", "model": "stub",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}}
