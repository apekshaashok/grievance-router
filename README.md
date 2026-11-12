# Securities Investor Grievance Routing Service (Mini Project 12)

UE24AM342AA5 - ML System Design & AgentOps. A multi-agent service that takes an investor complaint, validates it, decides which
regulator (SEBI or RBI) and which entity are responsible from the regulations, and routes it with cited evidence - or escalates to
a human when it cannot answer. It never disposes of a complaint.

The section 4.1 case ("I invested in a fixed deposit through my bank's app ... I want to complain to SEBI") is routed to the
**RBI Integrated Ombudsman Scheme** because the agent identifies a bank deposit, searches for the framework covering banks, reads its
scope clauses, checks maintainability, and verifies every citation against retrieved text before answering.

## Read this first: what is real and what is a placeholder

| Item | Status |
|---|---|
| **Corpus** | `corpus/*.txt` are short **illustrative samples** so everything runs on a clean clone. They are *not* the official text and the clause numbers/values are not the real ones. **You must replace them with the real SEBI circular, SEBI MF Regulations chapter and RBI Integrated Ombudsman Scheme** (see `corpus/README.md`) and re-derive `eval/scenarios.json`. |
| **Model** | The default "model" is a deterministic rule engine, served by the `stub-llm` container (also the load-test stub, D6, and the fallback when the provider is down). The OpenAI-compatible provider path is tested **only against the stub**. It has not been run against a real provider or real LLM prompt behaviour: do that before the demo (set `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` in `.env`), then re-run the eval for the numbers in your report. |
| Tested here | 18 automated tests (`pytest -q`); 29-scenario eval; API + Redis + stub + 2 workers as separate processes (real Redis server) including provider failure -> breaker -> degraded run -> recovery; Locust SLO check against that stack. |
| **Not run here** | `docker compose up` itself (no Docker in the build sandbox: files were validated by parsing only), Prometheus/Grafana/Jaeger/cAdvisor, OpenTelemetry export, the sentence-transformers embedder, real-PDF extraction. Expect to fix small things on first `docker compose up` and log them in `docs/incident_log.md`. |
| Numbers | Any figure you put in your report must come from **your** runs. Nothing measured in the sandbox is in the docs except the sample-corpus eval comparisons quoted in `docs/architecture.md`. |

## Quick start

```bash
git clone <your repo> && cd <repo>
docker compose up --build            # needs Docker Compose v2.24+; first build takes a few minutes
```
Then: web client http://localhost:8000 | API docs http://localhost:8000/docs | Grafana http://localhost:3000 (dashboard "Grievance Router") |
Jaeger http://localhost:16686 | Prometheus http://localhost:9090 | Locust `docker compose --profile load up locust` http://localhost:8089

```bash
docker compose up -d --scale worker=3                 # A7
python scripts/demo.py fd                             # the section 4.1 case from the command line
python scripts/demo.py trace <run id>                 # every tool call, query, passage, prompt version, latency, cost
```
Configuration: copy `.env.example` to `.env` and change what you need (the committed `.env.example` already works as defaults).
No credentials, endpoints or model identifiers are in code; they are runtime configuration.

Without Docker (development):
```bash
pip install -r requirements-dev.txt
export REDIS_URL=redis://localhost:6379/0 INDEX_DIR=data/index LLM_PROVIDER=rules
python -m app.ingest && uvicorn app.api:app &        # needs a Redis server; the tests use an in-memory one
python -m app.worker &
pytest -q
```

## Evaluation (L2, L3)
```bash
python -m eval.run_eval -v                                                   # 29 scenarios incl. the 4.1 case
python -m eval.run_eval --variants PROMPT_VERSION=v1 PROMPT_VERSION=v2       # L2: compare two prompt variants
python -m eval.run_eval --variants CHUNKING=clause CHUNKING=fixed            # L3: one deliberate change, all metrics before/after
```
Metrics (definitions in `eval/metrics.py`): groundedness, context relevance (+ gold recall), faithfulness, hallucination rate - lexical, claim-level equivalents
of the TruLens measures (no TruLens dependency; say so in your report, or add TruLens as an extension). They are also reported *before* verification, which is what shows the
verifier doing its job. Cost per 100 runs and the largest cost driver are printed at the end. Per-scenario results: `eval/results/*.json`.
**Caveat**: with the rule engine as the model, the sample corpus and scenarios written by the same author, scores near 1.0 are expected and prove the plumbing, not real-world quality. Run with a real model on the real corpus for the report.

## Requirements map

| Req | Where |
|---|---|
| F1-F2 REST + contract + validation + quarantine | `app/api.py`, `app/contracts.py`, `docs/data_contract.md` |
| F3-F6 classify, retrieve, determine, route | `app/agents/` (`policy.py` rules engine, `tools.py`, `verifier.py`), `prompts/` |
| F7 escalate | verifier + `graph.py` escalation paths; reasons in `contracts.py` |
| F8 health, metrics, logs | `/healthz` `/readyz` `/metrics`, `app/logging_setup.py`, `app/metrics.py` |
| A1 LangGraph, 4 tools, bounded loop | `app/agents/graph.py`, `tools.py`, `MAX_STEPS/MAX_TOOL_CALLS/RUN_TIMEOUT_S` |
| A2 two agents + hand-off + failure | `docs/architecture.md` ("The agents") |
| A3 verify before returning | `app/agents/verifier.py`, `graph.py:n_verify` |
| A4 FastAPI async + queue | `app/api.py`, `app/store.py`, `app/worker.py`, `docs/architecture.md` |
| A5 timeouts, backoff, breaker, degradation | `app/llm/resilience.py`, tool timeouts in `graph.py`, tests in `tests/test_agent_and_resilience.py` |
| A6 per-agent metrics + dashboard | `app/metrics.py`, `monitoring/grafana/dashboards/` (20 panels) |
| A7 `--scale` | `docker-compose.yml` (`worker`), Prometheus DNS service discovery, `docs/slo.md` |
| A8 web client | `web/index.html` |
| L1 chunking, embeddings, re-embedding | `app/retrieval/`, `app/ingest.py` |
| L2 versioned prompts + comparison | `prompts/`, `app/prompts.py`, `eval.run_eval --variants` |
| L3 eval set + metrics | `eval/` |
| L4 guardrails | `app/guardrails.py` |
| L5 tokens/cost per step | `graph.py:_account`, trace, `eval.run_eval` summary, Grafana |
| L6 feedback + live quality sampling | `/v1/runs/{id}/feedback`, `/v1/quality`, `app/worker.py:quality_sample` |
| D1 contracts + quarantine | `app/contracts.py`, `/v1/quarantine` |
| D2 idempotent pipeline + freshness | `app/ingest.py`, `index_meta.json`, `index_*` metrics |
| D3 >=4 services, health checks | `docker-compose.yml` (10 services), justification in `docs/architecture.md` |
| D4 no credentials/endpoints/models in repo | `.env.example`, `app/config.py` |
| D5 logs, correlation id, OTel, Prometheus, Grafana, run reconstruction | `logging_setup.py`, `telemetry.py`, `monitoring/`, `GET /v1/runs/{id}/trace` |
| D6 SLO + Locust + stub | `docs/slo.md`, `load/locustfile.py`, `app/stub_llm.py` |
| D7 caching + rate limiting | `app/store.py`, `api.py` |
| D8 runbook + incident log | `docs/runbook.md`, `docs/incident_log.md` |

## Demonstration checklist (section 9)

| # | Do |
|---|---|
| 1 | `docker compose up --build` from a clean clone |
| 2 | `python scripts/demo.py valid` (or the web client) |
| 3 | `python scripts/demo.py trace <run id>`; also Jaeger |
| 4 | `python scripts/demo.py malformed`, then `GET /v1/quarantine`; `runs_submitted_total` and queue unchanged |
| 5 | `python scripts/demo.py outofcorpus` |
| 6 | `python scripts/demo.py unsupported` (needs `ENABLE_FAULT_INJECTION=1`); open the trace: first verify fails, second passes |
| 7 | `python scripts/demo.py injection` |
| 8 | `docker compose stop stub-llm` then `python scripts/demo.py fd`; show Grafana breaker panel; `docker compose start stub-llm` |
| 9 | Locust against the SLO in `docs/slo.md`; repeat with `--scale worker=3` |
| 10 | `python scripts/demo.py fd` |

## Before you submit - checklist

1. Replace the corpus with the real documents; build the index; check `python -m app.ingest --dump`.
2. At Evaluation 1 show the passages the 4.1 case depends on (the RBI scope clauses on deposits/banks, the prior-complaint clause) and the values you test with. Run the single-pass baseline on the real corpus and report honestly what it does.
3. Re-derive `eval/scenarios.json` expectations (timeline values, clause ids) from the real text.
4. Configure a real model, set a spending limit on the account, re-run eval and the demo; fill your own numbers into the report.
5. Edit and **commit `docs/slo.md` before any load test**; fill the results table.
6. Run `docker compose up` from a fresh clone on a second machine or after `docker compose down -v`; add every real failure to `docs/incident_log.md`.
7. Fill `docs/report_template.md`; state what you did not do.

## Be ready to explain (viva)

* Why is the 4.1 case not solvable by one retrieval pass, and which step of the agent fixes it?
* Which checks can stop a wrong answer, and what can they not catch? (Grounded-but-wrong: see baseline scenarios S19/S21/S23.)
* What happens, step by step, when the provider goes down? When a worker dies mid-run?
* Why async + queue + polling? Why one `worker` container for two agents?
* How is "idempotent" achieved in ingestion? How do you re-embed one document?
* What does each of groundedness / faithfulness / context relevance / hallucination rate measure, and why do they differ?
* Why does the load test use a stub, and what would change with a real provider?
