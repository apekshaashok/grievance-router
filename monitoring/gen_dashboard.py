"""Generates monitoring/grafana/dashboards/grievance-router.json. Edit panels here, re-run, commit the JSON."""
import json

panels = []


def P(title, exprs, unit="short"):
    i = len(panels)
    panels.append({"id": i + 1, "title": title, "type": "timeseries", "datasource": {"type": "prometheus", "uid": "prom"},
                   "gridPos": {"x": (i % 3) * 8, "y": (i // 3) * 7, "w": 8, "h": 7},
                   "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
                   "targets": [{"expr": e, "legendFormat": l, "refId": chr(65 + n)} for n, (e, l) in enumerate(exprs)]})


def q(p, m, extra=""):
    return f"histogram_quantile({p}, sum by (le{extra}) (rate({m}_bucket[1m])))"


P("Run latency p50 / p95 vs SLO (submission to result)", [(q(.5, "run_e2e_seconds"), "p50"), (q(.95, "run_e2e_seconds"), "p95"), ("max(slo_p95_target_seconds)", "SLO p95 target")], "s")
P("Throughput (runs/s), submissions, queue depth", [("sum(rate(runs_completed_total[1m]))", "completed"), ("sum(rate(runs_submitted_total[1m]))", "submitted"), ("sum(queue_depth)", "queue depth")])
P("Error rate", [('sum(rate(runs_completed_total{outcome="failed"}[1m])) / clamp_min(sum(rate(runs_completed_total[1m])),1e-9)', "failed runs"), ('sum(rate(http_requests_total{status=~"5.."}[1m])) / clamp_min(sum(rate(http_requests_total[1m])),1e-9)', "HTTP 5xx")], "percentunit")
P("Availability (up)", [("avg by (job) (up)", "{{job}}")], "percentunit")
# cAdvisor names containers on a Linux host; on Docker Desktop it cannot, so each app container's own process metrics
# (one process per container; worker replicas by instance) are shown alongside.
P("Container CPU (cores)", [('sum by (name) (rate(container_cpu_usage_seconds_total{name!=""}[1m]))', "{{name}}"),
                            ('sum by (job, instance) (rate(process_cpu_seconds_total{job=~"api|worker|stub-llm"}[1m]))', "{{job}} {{instance}}")])
P("Container memory", [('sum by (name) (container_memory_working_set_bytes{name!=""})', "{{name}}"),
                       ('sum by (job, instance) (process_resident_memory_bytes{job=~"api|worker|stub-llm"})', "{{job}} {{instance}}")], "bytes")
P("Task outcomes (task success rate)", [("sum by (outcome) (rate(runs_completed_total[1m]))", "{{outcome}}")])
P("Steps and tool calls per run (median)", [(q(.5, "run_agent_steps"), "steps p50"), (q(.5, "run_tool_calls"), "tool calls p50")])
P("Latency per step, p95", [(q(.95, "step_duration_seconds", ", step"), "{{step}}")], "s")
P("Retry rate", [("sum by (target) (rate(retries_total[1m]))", "{{target}}")])
P("Circuit breaker (1 = open) and degraded runs", [("max by (instance) (circuit_breaker_open)", "breaker {{instance}}"), ("sum(rate(degraded_runs_total[1m]))", "degraded runs/s")])
P("Verification and escalations", [("sum by (result) (rate(verification_total[1m]))", "verify {{result}}"), ("sum by (reason) (rate(escalations_total[1m]))", "escalate {{reason}}")])
P("Tokens per second by step", [("sum by (step) (rate(llm_tokens_total[1m]))", "{{step}}")])
P("Cost per second by step (USD)", [("sum by (step) (rate(llm_cost_usd_total[1m]))", "{{step}}")], "currencyUSD")
P("Cache hit ratio", [('sum by (layer) (rate(cache_events_total{result="hit"}[1m])) / clamp_min(sum by (layer) (rate(cache_events_total[1m])),1e-9)', "{{layer}}")], "percentunit")
P("Sampled quality over time", [("sum by (metric) (rate(quality_score_sum[5m])) / clamp_min(sum by (metric) (rate(quality_score_count[5m])),1e-9)", "{{metric}}")], "percentunit")
P("Guardrails and rejects", [("sum by (type) (rate(guardrail_events_total[1m]))", "guard {{type}}"), ("sum by (reason) (rate(requests_rejected_total[1m]))", "rejected {{reason}}")])
P("Container uptime (s)", [('time() - process_start_time_seconds{job=~"api|worker|stub-llm"}', "{{job}} {{instance}}")], "s")
P("Index freshness: age (s), chunks, ingest chunks/s", [("time() - max(index_built_timestamp_seconds)", "index age"), ("max(index_chunks)", "chunks"), ("max(ingest_chunks_per_second)", "ingest chunks/s")])
P("Caller feedback (last hour)", [("sum by (satisfactory) (increase(feedback_total[1h]))", "satisfactory={{satisfactory}}")])
dash = {"title": "Grievance Router", "uid": "grievance-router", "schemaVersion": 39, "version": 1, "refresh": "5s",
        "time": {"from": "now-15m", "to": "now"}, "panels": panels, "templating": {"list": []}}
json.dump(dash, open("monitoring/grafana/dashboards/grievance-router.json", "w"), indent=1)
print(len(panels), "panels")
