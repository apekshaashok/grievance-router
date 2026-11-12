# Runbook (D8) - the two most likely failure modes

## 1. Model provider unavailable or rate-limited (circuit breaker open)

**Symptom**: runs complete but the web client shows "DEGRADED MODE"; Grafana "Circuit breaker" panel shows 1; `degraded_runs_total`
rising; `retries_total{target="llm"}` rising; worker logs `circuit breaker opened`. If `LLM_FALLBACK=none`: runs end in `escalate-to-human`
with reason `provider_unavailable`. Latency is *lower* than normal in degraded mode (no model calls).

**Confirm**
```
docker compose logs worker | grep -E "llm call failed|circuit breaker"
curl -s localhost:8081/healthz              # stub; for a real provider, call its API with curl
curl -s localhost:9090/api/v1/query --data-urlencode 'query=max(circuit_breaker_open)'
```
A variant with a real model: runs escalate with `provider_unavailable` or `verification_failed` while the provider is up and
the trace shows model replies with empty content or cut-off JSON (`completion_tokens` at `LLM_MAX_TOKENS`). That is the
context window or the output limit, not an outage (INC-004): check the largest `prompt_tokens` in `GET /v1/runs/{id}/trace`
against the model's context (Ollama defaults to 4,096 tokens; use the 16k alias in the README).

**Recover**: fix the cause (quota, key in `.env`, network, provider outage; for the stub `curl -XPOST 'localhost:8081/admin/fail?on=false'`
or `docker compose start stub-llm`). The breaker half-opens after `BREAKER_COOLDOWN_S` and closes on the first successful call; no restart
is needed. Degraded answers were still verified against the corpus, but have a human look at runs flagged `degraded` in the period
(`GET /v1/runs/{id}`). To fail safe instead of degrading, set `LLM_FALLBACK=none` and `docker compose up -d`.

## 2. Queue backlog (workers down, stuck or too few)

**Symptom**: runs stay `queued`; `queue_depth` (Grafana "Throughput" panel) grows; `run_e2e_seconds` p95 climbs past the SLO while
`run_exec_seconds` stays normal; the web client keeps polling.

**Confirm**
```
docker compose ps worker                              # restarting / unhealthy?
docker compose logs --tail=50 worker                  # index missing? redis unreachable? "worker loop error"
docker compose exec redis redis-cli llen grievance:queue
docker compose exec redis redis-cli smembers runs:running   # runs a dead worker never finished
```
**Recover**: if workers are crash-looping fix the logged cause (typically the index volume is empty: `docker compose run --rm indexer`).
If load is simply higher than capacity: `docker compose up -d --scale worker=3`. Runs orphaned by a dead worker are re-queued
automatically by the reaper after `STALE_RUN_S` (120 s), twice at most, then marked `failed` (`worker_lost`). Clients are protected by the
per-client rate limit (429); raise `RATE_LIMIT_PER_MIN` only deliberately.
