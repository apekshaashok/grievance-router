# Architecture

## Topology and deployment boundaries (D3)

```
                      +-----------+   POST /v1/complaints (202 + run id)   +-----------+
   browser / curl --> |    api    | ------------- enqueue ---------------> |   redis   |  queue, run records, traces,
   (web/index.html)   | FastAPI   | <------ poll GET /v1/runs/{id} -------- |           |  quarantine, cache, rate limits
                      +-----------+                                         +-----+-----+
   validation, quarantine,   |  /metrics                                          | BRPOP
   rate limit, run cache     |                                           +--------v---------+      +-------------+
                             |                                           |  worker (x N)    | ---> |  stub-llm   |  OpenAI-compatible;
   indexer (one-shot) ------> shared volume: FAISS index + chunks         |  LangGraph:      |      |  or real    |  fixed answers after
   python -m app.ingest                                                   |  Investigator -> |      |  provider   |  a realistic delay
                                                                          |  Verifier/Router |      +-------------+
   prometheus <-- scrape api, every worker replica (DNS SD), cadvisor     +------------------+
   grafana  <-- prometheus          jaeger <-- OTLP traces from api and worker (one trace per run)
```

| Boundary (container) | Why it is separate |
|---|---|
| `api` | **Scaling / resource profile**: cheap, latency-sensitive, I/O bound. It must stay responsive while agent runs take tens of seconds. |
| `worker` | **Scaling and failure isolation**: CPU/wait heavy, scaled with `--scale worker=N`; a crash or a stuck model call loses a run (recovered by the reaper), never the API. |
| `redis` | **Failure isolation / independent release**: durable hand-off between api and workers (append-only file). |
| `indexer` | **Independent release / resource profile**: embedding is the only heavy batch job; it runs and exits, then publishes the index on a volume. Re-running it does not restart anything. |
| `stub-llm` | **Failure isolation**: the model is an external dependency; keeping it a separate service (even when a stub) means the circuit breaker, timeouts and degradation are exercised for real. |
| `prometheus`, `grafana`, `jaeger`, `cadvisor` | Observability stack, released independently of the application. |

Modules in the same container share a deployment boundary regardless of code layout: the Investigator and the Verifier are
two agents (separate prompts, responsibilities and failure behaviour) but deliberately **one deployable** (`worker`), because
they hand off in-memory state within one run and splitting them would add a network hop to every run for no scaling benefit.

## Why asynchronous, and why a queue (A4)

A run makes ~7 sequential model calls plus tool calls: tens of seconds with a real provider, so a synchronous HTTP call would
hold connections open, time out behind proxies, and tie the API's capacity to model latency. Instead `POST` validates and
returns `202` + a run id in milliseconds and the caller polls `GET /v1/runs/{id}`. Polling was chosen over callbacks because the
caller here is a browser or a script that cannot receive a callback, and polling needs no delivery guarantees.
A queue sits between submission and execution so that (1) a burst queues instead of exhausting workers or the provider quota,
(2) workers scale independently, (3) a crashed worker's run is still in Redis and is re-queued (`runs:running` + heartbeat, reaped
after `STALE_RUN_S`, at most twice, then marked `failed`).

## The agents (A1, A2, A3)

* **Investigator** (LLM + tools, LangGraph node loop). Chooses tools: `search_corpus`, `list_clauses`, `get_clause`,
  `get_complaint_fields`. It must identify the product and entity, search again with a query built from that, check what the
  candidate framework covers (scope), what bars the complaint (maintainability), the timeline (servicing), and follow every
  "See clause X" cross reference. Bounded by `MAX_STEPS`, `MAX_TOOL_CALLS`, `RUN_TIMEOUT_S`.
* **Verifier/Router** (separate prompt, no tools, the only component that can release an answer). Deterministic gate: the
  determination parses against the contract; every citation resolves to a retrieved passage; every claim is supported by the
  passage it cites (>=70% of content words, all numbers present); the route follows from what was cited (assign-to-entity needs a
  servicing passage that states the timeline and a scope passage of that regulator; refer-to-other-regulator needs the other
  regulator's scope passage; not-maintainable needs a maintainability passage; a split needs scope passages from two
  regulators); and the output filter passes. An optional model judge can add objections, never remove them.
* **Hand-off**: the Investigator's proposal plus the complete set of retrieved passages. On failure: retry with the issue list as
  hints (attempt 2, `MAX_VERIFY_ATTEMPTS`), then escalate `verification_failed`.
* **If an agent fails mid-run**: provider down -> retries with backoff -> breaker -> rule-engine fallback (run flagged
  `degraded`, still verified) -> if that is also impossible, escalate `provider_unavailable`. Verifier judge unavailable -> the
  deterministic checks alone decide (flagged degraded). Limits hit -> escalate `limits_exceeded`. Worker process dies -> reaper
  re-queues. Unhandled exception -> run `failed` with no determination. **No path returns a confident answer from an unfinished or unverified run.**

## Retrieval design (L1) - how chunking addresses section 3

The decisive text is never the text the complaint resembles. Chunks are **whole clauses**, never cut mid-provision, carrying
`regulator`, `doc_id`, `heading` and `kind` (scope / maintainability / servicing / general). The embedded text is prefixed with
the document title and heading. `kind` lets the agent ask "what does this framework cover?" (`list_clauses kind=scope`) instead of
hoping similarity finds it; `refs` extracted from "See clause ..." let it follow modifiers that sit in another clause or
document (`SEBI-MC#4.5 -> SEBI-MFR#2.2`). Embeddings: a local sentence-transformers model (named in `.env`, never in code), or the offline
hashing embedder by default. The index is keyed by chunk content hash: re-running ingestion embeds only new/changed chunks;
`--only DOC_ID` forces one document; the whole index is never rebuilt unless `--force`. Measured on the sample corpus:
clause chunking vs fixed-size windows moves final accuracy from 1.00 to 0.10 (`python -m eval.run_eval --variants CHUNKING=clause CHUNKING=fixed`).

## Caching and rate limiting (D7)

* Query-embedding cache (in-process LRU): agents re-issue near-identical queries; saves embedding time.
* Whole-run cache (Redis, keyed by normalised complaint fields + prompt version + index build): identical complaints are
  common (retries, double submits); a hit costs zero model calls. Callers can bypass with `Cache-Control: no-cache`.
  Not cached: degraded runs and fault-injected runs. The key includes the index build, so a re-index invalidates it.
* Per-client rate limit (fixed window, Redis, `X-Client-Id` or IP): a cost control - one client looping against a multi-step
  agent can exhaust a model budget in minutes.

## Guardrails (L4)

Injection: instruction-like text is detected and removed from every free-text field (`complaint_text`, `entity_name`) before any
model sees it, flagged in the response, and counted in a metric; remaining text reaches the model only as JSON string values
under a system rule that they are data; tools are an allow-list of read-only functions with validated arguments (an injected
instruction cannot reach a tool that writes, sends or approves, because none exists); the output filter blocks disposal language
("resolved", "approved", "refund will be paid"), PAN-like identifiers and abusive language, converting the result to an escalation.
Toxicity: a small mild-term list masks abusive input and blocks abusive output (extend `TOXIC_TERMS`; swap in a classifier if you have time).

## Alternatives rejected

* Single retrieval pass + one answer prompt: cannot return the scope clause of a framework the request never names (section 5).
* Synchronous request/response: ties API capacity to model latency (above).
* Callbacks instead of polling: the caller cannot receive them; adds delivery semantics.
* Celery/RQ: a plain Redis list + heartbeat reaper is ~40 lines, has no extra moving parts, and is easy to explain; revisit if you need scheduling or priorities.
* Separate containers for Investigator and Verifier: adds a hop per run for no scaling benefit (above).
* LLM-only verification: the model that can be wrong cannot be the only check on itself; deterministic checks are the gate.
