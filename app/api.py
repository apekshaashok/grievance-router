"""FastAPI service (A4, F1, F2, F8, D1, D5, D7, L6).

Runs take tens of seconds, so submission is asynchronous: POST returns 202 with a run id at once and the caller polls
GET /v1/runs/{id}. A queue (Redis) sits between submission and execution so that bursts queue instead of exhausting
workers, workers scale independently, and a worker crash loses nothing (see docs/architecture.md for the justification
against synchronous and callback designs).
"""
import logging
import os
import time
import uuid
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import guardrails, metrics as M
from .config import cfg
from .contracts import ComplaintIn, FeedbackIn, format_errors, validate_complaint
from .logging_setup import get_correlation_id, log, set_context, setup_logging
from .retrieval.index import index_exists, load_index, publish_index_metrics
from .store import Store
from .telemetry import init_tracing, inject_context, span
from pydantic import ValidationError

logger = logging.getLogger("api")
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
FAULTS = {"bad_citation", "wrong_timeline", "hallucinated_provision"}


def create_app(store: Optional[Store] = None) -> FastAPI:
    os.environ.setdefault("SERVICE_NAME", "api")
    setup_logging()
    init_tracing("grievance-api")
    M.SLO_TARGET.set(cfg.SLO_P95_SECONDS)
    app = FastAPI(title="Securities Investor Grievance Routing Service", version="1.0")
    holder = {"store": store}

    def get_store() -> Store:
        if holder["store"] is None:
            holder["store"] = Store()
        return holder["store"]

    @app.middleware("http")
    async def observe(request: Request, call_next):
        cid = request.headers.get("x-correlation-id") or uuid.uuid4().hex[:16]
        set_context(correlation_id=cid, run_id="-")
        t0 = time.time()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        dt = time.time() - t0
        M.HTTP_REQS.labels(request.method, path, str(response.status_code)).inc()
        M.HTTP_LAT.labels(path).observe(dt)
        response.headers["x-correlation-id"] = cid
        if path not in ("/metrics", "/healthz"):
            log(logger, "request", method=request.method, path=request.url.path, status=response.status_code, duration_s=round(dt, 4))
        return response

    # ------------------------------------------------------------------ submit
    def _submit(payload, headers: dict, client: str):
        s = get_store()
        cid = get_correlation_id()
        allowed, retry_after = s.rate_limit(client, cfg.RATE_LIMIT_PER_MIN)
        if not allowed:
            M.REJECTED.labels("rate_limited").inc()
            return 429, {"error": "rate_limited", "detail": f"limit is {cfg.RATE_LIMIT_PER_MIN} submissions per minute per client",
                         "retry_after_s": retry_after}, {"Retry-After": str(retry_after)}
        model, errors = validate_complaint(payload)          # ingestion boundary: nothing downstream sees invalid data
        if errors:
            qid = s.quarantine(payload, errors, client, cid)
            M.REJECTED.labels("validation").inc()
            log(logger, "request quarantined", quarantine_id=qid, n_errors=len(errors))
            return 422, {"error": "validation_failed", "quarantine_id": qid, "agent_started": False, "errors": errors}, {}
        fields = model.model_dump(mode="json")
        _, flags = guardrails.sanitize_fields(fields)
        run_id = uuid.uuid4().hex[:12]
        built_at = ""
        if index_exists():
            built_at = load_index().meta.get("built_at", "")
        cache_key = s.cache_key(fields, cfg.PROMPT_VERSION, built_at)
        fault = headers.get("x-debug-fault") if cfg.ENABLE_FAULT_INJECTION and headers.get("x-debug-fault") in FAULTS else None
        if "no-cache" not in headers.get("cache-control", "") and not fault:
            hit = s.cache_get(cache_key)
            if hit:
                M.CACHE.labels("run", "hit").inc()
                hit["latency"] = {"exec_s": 0.0, "e2e_s": 0.0}
                s.create_run(run_id, fields, client, cid, flags, status="completed", result={**hit, "run_id": run_id, "correlation_id": cid}, cache_hit=True)
                M.SUBMITTED.inc()
                M.RUNS_DONE.labels(hit["outcome"]).inc()
                M.RUN_E2E.observe(0.001)
                return 202, {"run_id": run_id, "status": "completed", "cache_hit": True, "status_url": f"/v1/runs/{run_id}",
                             "guardrails": flags}, {}
            M.CACHE.labels("run", "miss").inc()
        s.create_run(run_id, fields, client, cid, flags)
        s.enqueue({"run_id": run_id, "correlation_id": cid, "submitted_at": time.time(), "cache_key": cache_key,
                   "fault": fault, "otel": inject_context()})
        M.SUBMITTED.inc()
        log(logger, "run queued", run_id=run_id, injection_detected=flags["injection_detected"])
        return 202, {"run_id": run_id, "status": "queued", "cache_hit": False, "status_url": f"/v1/runs/{run_id}",
                     "guardrails": flags}, {}

    @app.post("/v1/complaints", status_code=202, summary="Submit a complaint for routing (asynchronous)")
    async def submit(request: Request):
        client = request.headers.get("x-client-id") or (request.client.host if request.client else "unknown")
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            payload = None
            raw = (await request.body())[:2000].decode("utf-8", "replace")
            s = get_store()
            qid = await run_in_threadpool(s.quarantine, {"raw": raw}, [{"field": "(body)", "message": "body is not valid JSON", "type": "json_invalid"}], client, get_correlation_id())
            M.REJECTED.labels("validation").inc()
            return JSONResponse({"error": "validation_failed", "quarantine_id": qid, "agent_started": False,
                                 "errors": [{"field": "(body)", "message": "body is not valid JSON"}]}, status_code=422)
        with span("api.submit", client=client):
            code, body, hdrs = await run_in_threadpool(_submit, payload, {k.lower(): v for k, v in request.headers.items()}, client)
        return JSONResponse(body, status_code=code, headers=hdrs)

    # ------------------------------------------------------------------ read
    @app.get("/v1/runs/{run_id}", summary="Status and result of a run")
    async def get_run(run_id: str):
        rec = await run_in_threadpool(get_store().get_run, run_id)
        if not rec:
            return JSONResponse({"error": "not_found"}, status_code=404)
        out = {"run_id": run_id, "status": rec["status"], "cache_hit": bool(int(rec.get("cache_hit", 0))),
               "correlation_id": rec.get("correlation_id"), "submitted_at": rec["submitted_at"]}
        if rec["status"] in ("completed", "failed"):
            out["result"] = rec.get("result")
        return out

    @app.get("/v1/runs/{run_id}/trace", summary="Everything needed to reconstruct a run")
    async def get_trace(run_id: str):
        s = get_store()
        rec = await run_in_threadpool(s.get_run, run_id)
        if not rec:
            return JSONResponse({"error": "not_found"}, status_code=404)
        events = await run_in_threadpool(s.get_trace, run_id)
        res = rec.get("result") or {}
        return {"run_id": run_id, "correlation_id": rec.get("correlation_id"), "status": rec["status"],
                "prompt_version": res.get("prompt_version"), "verification": res.get("verification"),
                "usage": res.get("usage"), "events": events or []}

    @app.post("/v1/runs/{run_id}/feedback", status_code=201, summary="Mark a result satisfactory or not")
    async def feedback(run_id: str, request: Request):
        s = get_store()
        if not await run_in_threadpool(s.get_run, run_id):
            return JSONResponse({"error": "not_found"}, status_code=404)
        try:
            body = FeedbackIn.model_validate(await request.json())
        except ValidationError as exc:
            return JSONResponse({"error": "validation_failed", "errors": format_errors(exc)}, status_code=422)
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "validation_failed", "errors": [{"field": "(body)", "message": "invalid JSON"}]}, status_code=422)
        await run_in_threadpool(s.add_feedback, run_id, body.satisfactory, body.reason)
        M.FEEDBACK.labels(str(body.satisfactory).lower()).inc()
        return {"ok": True}

    @app.get("/v1/feedback/summary")
    async def feedback_summary():
        return await run_in_threadpool(get_store().feedback_summary)

    @app.get("/v1/quality", summary="Sampled quality scores of live runs over time")
    async def quality(limit: int = 200):
        return {"samples": await run_in_threadpool(get_store().list_quality, min(limit, 1000))}

    @app.get("/v1/quarantine", summary="Rejected requests with field-level errors")
    async def quarantine(limit: int = 50):
        return {"records": await run_in_threadpool(get_store().list_quarantine, min(limit, 500))}

    @app.get("/v1/contract", summary="Published data contract (JSON Schema)")
    async def contract():
        return ComplaintIn.model_json_schema()

    # ------------------------------------------------------------------ ops
    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz():
        checks = {}
        try:
            checks["redis"] = await run_in_threadpool(get_store().ping)
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = False
            checks["redis_error"] = str(exc)[:100]
        checks["index"] = index_exists()
        ok = checks["redis"] and checks["index"]
        return JSONResponse({"status": "ready" if ok else "not_ready", **checks}, status_code=200 if ok else 503)

    @app.get("/metrics")
    async def metrics():
        try:
            M.QUEUE_DEPTH.set(await run_in_threadpool(get_store().depth))
        except Exception:  # noqa: BLE001
            pass
        publish_index_metrics()
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/", include_in_schema=False)
    async def web_client():
        return FileResponse(os.path.join(WEB_DIR, "index.html"))

    return app


app = create_app()
