"""Prometheus metrics for every service (A6, D2, D5, D7, L5, L6)."""
from prometheus_client import Counter, Gauge, Histogram

_SECS = (0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144)

HTTP_REQS = Counter("http_requests_total", "HTTP requests", ["method", "path", "status"])
HTTP_LAT = Histogram("http_request_duration_seconds", "HTTP latency", ["path"], buckets=_SECS)
SUBMITTED = Counter("runs_submitted_total", "Runs accepted for execution")
REJECTED = Counter("requests_rejected_total", "Requests refused before any agent ran", ["reason"])
CACHE = Counter("cache_events_total", "Cache lookups", ["layer", "result"])
QUEUE_DEPTH = Gauge("queue_depth", "Runs waiting in the queue")

RUNS_DONE = Counter("runs_completed_total", "Finished runs", ["outcome"])  # routed | escalated | failed
RUN_E2E = Histogram("run_e2e_seconds", "Submission to final result (includes queue wait)", buckets=_SECS)
RUN_EXEC = Histogram("run_exec_seconds", "Worker execution time of one run", buckets=_SECS)
STEP_LAT = Histogram("step_duration_seconds", "Latency per agent step", ["step"], buckets=_SECS)
RUN_STEPS = Histogram("run_agent_steps", "LLM steps per run", buckets=(1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20))
RUN_TOOLS = Histogram("run_tool_calls", "Tool calls per run", buckets=(0, 1, 2, 3, 4, 5, 6, 8, 10, 15))
RETRIES = Counter("retries_total", "Retries", ["target"])
LLM_CALLS = Counter("llm_calls_total", "LLM calls", ["provider", "status"])
BREAKER_OPEN = Gauge("circuit_breaker_open", "1 while the LLM circuit breaker is open")
DEGRADED = Counter("degraded_runs_total", "Runs that used the fallback path")
ESCALATIONS = Counter("escalations_total", "Escalations to a human", ["reason"])
VERIFY = Counter("verification_total", "Verification outcomes", ["result"])
TOKENS = Counter("llm_tokens_total", "Tokens", ["kind", "step"])
COST = Counter("llm_cost_usd_total", "Cost at time of call", ["step"])
GUARD = Counter("guardrail_events_total", "Guardrail activations", ["type"])
QUALITY = Histogram("quality_score", "Sampled live-run quality", ["metric"], buckets=(0, .2, .4, .6, .8, .9, 1.0))
FEEDBACK = Counter("feedback_total", "Caller feedback", ["satisfactory"])

INDEX_CHUNKS = Gauge("index_chunks", "Chunks in the index")
INDEX_BUILT_TS = Gauge("index_built_timestamp_seconds", "When the index was built")
INGEST_THROUGHPUT = Gauge("ingest_chunks_per_second", "Throughput of the last ingestion run")
SLO_TARGET = Gauge("slo_p95_target_seconds", "Declared p95 SLO for a complete run")
