# Securities Investor Grievance Routing Service (Mini Project 12)

UE24AM342AA5, ML System Design & AgentOps. A multi-agent service that takes a (synthetic) investor complaint, validates it,
works out from the regulations which regulator (SEBI or RBI) and which entity is responsible, and routes it with circular-level
citations, or escalates to a human when it cannot answer safely. It never disposes of a complaint; a grievance officer decides.

The section 4.1 case ("I invested in a fixed deposit through my bank's app ... I want to complain to SEBI") is routed to the
**RBI Integrated Ombudsman Scheme**, citing SEBI's own exclusion (`SEBI-SCORES#AII-5.3`: complaints within RBI's purview are not
dealt through SCORES) and the RBI clauses that cover banks (`RBI-IOS#3(1)(e)`, `3(1)(j)`, `9`). Details: `docs/eval1_reference_case.md`.

| Document | What it is |
|---|---|
| `docs/report.md` | The project report: design, evaluation before/after, SLO and load test, cost, what did not work |
| `docs/architecture.md` | Topology, deployment boundaries, agents, retrieval design, alternatives rejected |
| `docs/eval1_reference_case.md` | The 4.1 case: passages it depends on, test values, why one retrieval pass fails |
| `docs/data_contract.md` | Data contract of every external interface (also `GET /v1/contract`) |
| `docs/slo.md` | p95 SLO (declared and committed before measuring) and load-test results |
| `docs/runbook.md`, `docs/incident_log.md` | The two most likely failure modes; real failures met during development |
| `corpus/README.md` | The three regulatory documents, versions, provenance, how they were converted |
| `eval/scenarios.json` | The 34-scenario evaluation set with expected outcomes and supporting passages |

## 1. Run it

**Prerequisites**: Docker Desktop (or Docker Engine) with Compose v2.24+, ~6 GB free disk, ~4 GB RAM for the containers, and
internet access on the first start (images, Python wheels, and the ~90 MB embedding model).

```bash
git clone <this repository> grievance-router && cd grievance-router
docker compose up --build
```

The first build takes several minutes (it installs CPU-only PyTorch for the local embedding model). Start-up order is handled
by Compose: `redis` -> `indexer` (one-shot: downloads the embedding model into a volume and builds the index, then exits)
-> `api` and `worker`. The system is ready when `docker compose ps` shows `api` as `healthy`.

| URL | What |
|---|---|
| http://localhost:8000 | Web client: submit a complaint, see the route, citations and escalation path |
| http://localhost:8000/docs | REST API (OpenAPI) |
| http://localhost:3000 | Grafana, dashboard "Grievance Router" (no login) |
| http://localhost:16686 | Jaeger: one trace per run across api and worker |
| http://localhost:9090 | Prometheus |

From a terminal (Python 3.9+, standard library only):

```bash
python3 scripts/demo.py fd                  # the section 4.1 case
python3 scripts/demo.py trace <run id>      # every tool call, query, passage, prompt version, latency, tokens, cost, verification
```

Or with curl:

```bash
curl -s -XPOST localhost:8000/v1/complaints -H 'Content-Type: application/json' -d '{"investor_ref":"INV100001",
  "channel":"web","complaint_date":"2026-10-20","complaint_text":"My stock broker executed unauthorised trades in my trading account."}'
# -> 202 {"run_id": "...", "status": "queued", "status_url": "/v1/runs/<id>"}
curl -s localhost:8000/v1/runs/<id>          # poll until status is completed
```

**Configuration.** Defaults come from the committed `.env.example`; put your overrides in a git-ignored `.env` (same keys).
No credentials, endpoints or model identifiers are in the code. Out of the box the "model" is the `stub-llm` container: it
answers like a model after a realistic delay, with the deterministic rule engine behind it. That makes a clean clone work
with no API key, and it is what the load test uses (D6).

### Using a real model

Any OpenAI-compatible endpoint works; set three values in `.env` and restart the workers:

```bash
# a provider's free tier                       # or a local model with Ollama (8B-class or larger; see INC-003)
LLM_BASE_URL=https://<provider>/v1             LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_MODEL=<model name>                         LLM_MODEL=gemma4-16k
LLM_API_KEY=<your key>                         LLM_API_KEY=
```
```bash
docker compose up -d worker
```

With Ollama, give the model a context window large enough for the agent (Ollama defaults to 4,096 tokens, which truncates
prompts; INC-004), and raise the timeouts for local inference:

```bash
printf 'FROM gemma4\nPARAMETER num_ctx 16384\n' > Modelfile && ollama create gemma4-16k -f Modelfile
# in .env also:  LLM_TIMEOUT_S=240   RUN_TIMEOUT_S=900   LLM_REASONING_EFFORT=none  (gemma4 is a thinking model; INC-010)
```

Set a spending limit on any paid account first. `LLM_FALLBACK=rules` (default) degrades to the rule engine when the provider is
down (runs are flagged `degraded`); `LLM_FALLBACK=none` escalates instead.

## 2. Demonstration checklist (section 9 of the brief)

| # | Item | How |
|---|---|---|
| 1 | Clean clone to working system | `docker compose down -v` then `docker compose up --build` |
| 2 | Valid request, routed with citations that resolve to retrieved text | `python3 scripts/demo.py valid` or the web client |
| 3 | One run traced end to end | `python3 scripts/demo.py trace <run id>`; Jaeger, service `grievance-worker` |
| 4 | Malformed request quarantined, no agent run | `python3 scripts/demo.py malformed`, then `curl localhost:8000/v1/quarantine`; `runs_submitted_total` unchanged |
| 5 | Corpus cannot answer -> escalation | `python3 scripts/demo.py outofcorpus` (insurance: `entity_out_of_corpus`) |
| 6 | Self-verification catches an unsupported answer | `python3 scripts/demo.py unsupported` (injects a fabricated citation; first verify fails, agent re-searches, second passes). With a real model the 4.1 case often shows this unaided (`docs/evidence/`) |
| 7 | Prompt injection and the guardrail | `python3 scripts/demo.py injection` |
| 8 | Circuit breaker and degradation | `docker compose stop stub-llm`, `python3 scripts/demo.py fd` (degraded, still verified), Grafana "Circuit breaker" panel; `docker compose start stub-llm` |
| 9 | Locust vs the SLO, failing concurrency, `--scale` | `docker compose up -d --scale worker=1`, `scripts/loadtest.sh 8 3m w1`; repeat with `--scale worker=3`; results in `docs/slo.md` |
| 10 | The section 4.1 case | `python3 scripts/demo.py fd` |

## 3. Evaluation, tests and the corpus pipeline (no Docker needed)

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt -r requirements-st.txt
export KMP_DUPLICATE_LIB_OK=TRUE          # macOS only: faiss and torch both ship OpenMP (INC-008)
pytest -q                                 # 25 tests; offline (hash embedder, rule engine, in-memory Redis)

python -m eval.run_eval -v                                             # 34 scenarios incl. the 4.1 case
python -m eval.run_eval --variants PROMPT_VERSION=v1 PROMPT_VERSION=v2   # L2: compare two prompt versions
python -m eval.run_eval --variants CHUNKING=clause CHUNKING=fixed        # L3: one deliberate change, all metrics before/after
python -m app.ingest --dump                                              # the 64 indexed clauses with kind and cross references
```
Evaluation settings come from the environment, like the service (e.g. `EMBEDDER=sentence-transformers EMBED_MODEL=all-MiniLM-L6-v2`,
`LLM_PROVIDER=openai_compat LLM_BASE_URL=... LLM_MODEL=...`). With the default rule engine results are exactly repeatable; the
numbers in the report that describe model quality come from a real model (`eval/real/`). Metric definitions: `eval/metrics.py`.

## 4. Requirements map

| Req | Where |
|---|---|
| F1, F2, D1 contract, validation, quarantine | `app/contracts.py`, `app/api.py`, `docs/data_contract.md`, `GET /v1/contract`, `GET /v1/quarantine` |
| F3-F6 classify, retrieve, determine, route | `app/agents/` (`graph.py`, `tools.py`, `verifier.py`, `policy.py`), `prompts/` |
| F7 escalate | Verifier + escalation paths in `graph.py`; reasons in `contracts.py` |
| F8 health, metrics, logs | `/healthz`, `/readyz`, `/metrics`, JSON logs (`app/logging_setup.py`) |
| A1 LangGraph, 4 tools, bounded loop | `app/agents/graph.py`, `tools.py`; `MAX_STEPS`, `MAX_TOOL_CALLS`, `RUN_TIMEOUT_S` |
| A2 two agents, hand-off, failure | Investigator + Verifier/Router, `docs/architecture.md` "The agents" |
| A3 verify before returning | `app/agents/verifier.py`, `graph.py:n_verify` |
| A4 async FastAPI + queue | `app/api.py`, `app/store.py` (Redis queue), `app/worker.py` |
| A5 timeouts, backoff, breaker, degradation | `app/llm/resilience.py`, tool timeouts in `graph.py`, tests in `tests/` |
| A6 per-agent metrics on the dashboard | `app/metrics.py`, `monitoring/grafana/dashboards/grievance-router.json` |
| A7 `--scale worker=3` | `docker-compose.yml`, Prometheus DNS discovery, results in `docs/slo.md` |
| A8 web client | `web/index.html` |
| L1 chunking, embeddings, re-embedding | `app/retrieval/`, `app/ingest.py`, `corpus/README.md` |
| L2 versioned prompts + comparison | `prompts/*_v1.txt`, `*_v2.txt`; version + hash recorded in every response and trace |
| L3 eval set + metrics, before/after | `eval/`, `docs/report.md` |
| L4 guardrails | `app/guardrails.py`, `tools.py` (read-only allow-list) |
| L5 tokens and cost per step and run | `graph.py:_account`, trace, Grafana, eval summary |
| L6 feedback + live quality sampling | `POST /v1/runs/{id}/feedback`, `/v1/feedback/summary`, `/v1/quality`, `worker.py:quality_sample` |
| D2 idempotent ingestion + freshness | `app/ingest.py`, `index_meta.json`, `index_*` metrics |
| D3 services, health checks, boundaries | `docker-compose.yml` (9 services + Locust), `docs/architecture.md` |
| D4 configuration at runtime | `.env.example`, `app/config.py` |
| D5 logs, correlation id, tracing, dashboard, reconstruction | `logging_setup.py`, `telemetry.py`, `monitoring/`, `GET /v1/runs/{id}/trace` |
| D6 SLO + Locust + stub | `docs/slo.md`, `load/locustfile.py`, `scripts/loadtest.sh`, `app/stub_llm.py` |
| D7 caching + rate limiting | `app/store.py`, `app/api.py`, `app/retrieval/embedder.py` |
| D8 runbook + incident log | `docs/runbook.md`, `docs/incident_log.md` |

## 5. Troubleshooting

* `api` never becomes healthy: `docker compose logs indexer`. The indexer must finish first; it needs internet once to download the embedding model.
* Runs stay `queued`: `docker compose ps worker`, then `docs/runbook.md` section 2.
* Every run escalates with a real model: `docs/runbook.md` section 1 (context window and output limit).
* Port already in use: stop the other service or change the host port in `docker-compose.yml`.
