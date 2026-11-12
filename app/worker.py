"""Agent worker: pulls runs from the queue and executes them. Scale with `docker compose up --scale worker=3` (A7)."""
import logging
import random
import signal
import threading
import time

from prometheus_client import start_http_server

from . import metrics as M
from .agents.evidence import complaint_evidence
from .agents.graph import run_agent
from .config import cfg
from .logging_setup import log, set_context, setup_logging
from .retrieval.index import load_index, publish_index_metrics
from .store import Store
from .telemetry import attach_context, init_tracing, span

logger = logging.getLogger("worker")


def quality_sample(result: dict, retrieved: dict, fields: dict) -> dict:
    """Score a live run automatically (L6). No gold labels exist live, so relevance is a utilisation proxy."""
    from eval.metrics import claim_metrics
    cm = claim_metrics(result["determination"], {k: v["text"] for k, v in retrieved.items()}, fields)
    used = {c["id"] for c in result["citations"]}
    util = (len(used) / len(retrieved)) if retrieved else None
    return {"run_id": result["run_id"], "at": time.time(), "route": result["determination"]["route"],
            "groundedness": cm["groundedness"], "faithfulness": cm["faithfulness"],
            "context_utilisation": util, "hallucinated": cm["hallucinated"], "prompt_version": result["prompt_version"]}


def process_message(store: Store, msg: dict):
    run_id = msg["run_id"]
    set_context(msg.get("correlation_id") or run_id, run_id)
    rec = store.get_run(run_id)
    if rec is None or rec.get("status") in ("completed", "failed"):
        return
    with attach_context(msg.get("otel")), span("worker.run", run_id=run_id, correlation_id=msg.get("correlation_id", "")):
        store.mark_running(run_id)
        log(logger, "run started", queue_wait_s=round(time.time() - rec["submitted_at"], 3))
        outcome, t_start = "failed", time.time()
        try:
            fault = msg.get("fault") if cfg.ENABLE_FAULT_INJECTION else None
            result = run_agent(rec["payload"], run_id=run_id, correlation_id=msg.get("correlation_id"), fault=fault,
                               heartbeat=lambda: store.heartbeat(run_id))
            retrieved, trace = result.pop("_retrieved"), result.pop("_trace")
            result["latency"] = {"exec_s": result["exec_s"], "e2e_s": round(time.time() - rec["submitted_at"], 3)}
            store.save_trace(run_id, trace)
            store.finish_run(run_id, "completed", result)
            outcome = result["outcome"]
            if msg.get("cache_key") and not result["degraded"]:
                store.cache_set(msg["cache_key"], {k: v for k, v in result.items() if k not in ("run_id", "correlation_id", "latency")})
            if random.random() < cfg.QUALITY_SAMPLE_RATE:
                q = quality_sample(result, retrieved, rec["payload"])
                store.add_quality(q)
                for m in ("groundedness", "faithfulness", "context_utilisation"):
                    if q[m] is not None:
                        M.QUALITY.labels(m).observe(q[m])
            log(logger, "run finished", outcome=outcome, route=result["determination"]["route"], steps=result["steps"],
                tool_calls=result["tool_calls"], attempts=result["attempts"], degraded=result["degraded"],
                prompt_version=result["prompt_version"], cost_usd=result["usage"]["total"]["cost_usd"])
        except Exception as exc:  # noqa: BLE001 - a crashed run is reported as failed, never as an answer
            log(logger, "run failed", level="error", error=f"{type(exc).__name__}: {exc}")
            store.finish_run(run_id, "failed", {"error": type(exc).__name__, "message": str(exc)[:300]})
        finally:
            M.RUNS_DONE.labels(outcome).inc()
            M.RUN_E2E.observe(time.time() - rec["submitted_at"])
            M.RUN_EXEC.observe(time.time() - t_start)


def main():
    setup_logging()
    cfg_name = "worker"
    import os
    os.environ.setdefault("SERVICE_NAME", cfg_name)
    init_tracing("grievance-worker")
    M.SLO_TARGET.set(cfg.SLO_P95_SECONDS)
    load_index()  # fail fast if the index is missing
    publish_index_metrics()
    start_http_server(cfg.METRICS_PORT)
    store = Store()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    def loop():
        while not stop.is_set():
            try:
                msg = store.pop(timeout=2)
                if msg:
                    process_message(store, msg)
            except Exception as exc:  # noqa: BLE001
                log(logger, "worker loop error", level="error", error=str(exc))
                time.sleep(1)

    def reaper():
        while not stop.is_set():
            try:
                for item in store.requeue_stale(cfg.STALE_RUN_S):
                    log(logger, "recovered stale run", level="warning", action=item)
                publish_index_metrics()
            except Exception as exc:  # noqa: BLE001
                log(logger, "reaper error", level="error", error=str(exc))
            stop.wait(15)

    threads = [threading.Thread(target=loop, daemon=True) for _ in range(cfg.WORKER_CONCURRENCY)]
    threads.append(threading.Thread(target=reaper, daemon=True))
    for t in threads:
        t.start()
    log(logger, "worker started", concurrency=cfg.WORKER_CONCURRENCY, metrics_port=cfg.METRICS_PORT)
    stop.wait()
    log(logger, "worker stopping")
    for t in threads:
        t.join(timeout=5)


if __name__ == "__main__":
    main()
