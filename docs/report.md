# Report: Securities Investor Grievance Routing Service (Mini Project 12)

## 1. Problem and scope

Retail investors write free-text complaints to SEBI about brokers, mutual funds, registrars, listed companies and
depositories. Two decisions come first: **which regulator** (a bank product belongs to the RBI Ombudsman, not SEBI) and
**which entity**, since that fixes the redressal timeline. Routing to the wrong regulator delays redress.

The service accepts a complaint over REST, validates it at the boundary, and returns one of four routes:
`assign-to-entity` with a timeline, `refer-to-other-regulator`, `not-maintainable` with the reason, or `escalate-to-human`.
Every route comes with clause-level citations that resolve to retrieved text. **It never disposes of a complaint**: a
grievance officer decides, and an output filter blocks disposal language.

Corpus (real documents, `corpus/README.md`): SEBI circular SEBI/HO/OIAE/IGRD/CIR/P/2023/156 on SCORES (which rescinded the
2022 SCORES master circular), the investor-servicing provisions of the SEBI (Mutual Funds) Regulations 1996 (consolidated
Dec 2024), and the Reserve Bank - Integrated Ombudsman Scheme 2021 (as amended 2022): 64 clause-level chunks.
Synthetic complaints only; local deployment under Docker Compose; no model training.

## 2. Agent design

**Topology** (LangGraph, `app/agents/graph.py`):

```
START -> investigate <-> tools        Investigator: model chooses one tool per step (bounded loop)
            |
            v
          verify --fail, attempts left--> investigate (with the Verifier's issue list)
            |   \--fail, out of attempts--> escalate
            v
          finalize (output filter) -> END
```

* **Investigator** (prompt `investigator_v2`): chooses among 4 read-only tools: `search_corpus` (semantic, optional
  regulator/kind filter), `list_clauses` (a framework's scope / maintainability / servicing clauses), `get_clause` (exact
  clause id, follows cross references), `get_complaint_fields` (the request's own structured fields).
* **Verifier/Router**: the only component that can release an answer. Deterministic gate first: schema; every citation
  resolves to a retrieved passage; every claim is supported by the passage it cites (≥70% of content words, all numbers
  present); the route follows from what is cited (an assignment needs a scope clause and a servicing clause stating that
  exact timeline; a referral needs the other regulator's scope clause and, if the investor addressed SEBI, the SEBI
  exclusion; not-maintainable needs a maintainability clause; a split needs scope clauses from both regulators); the
  output filter. Then an optional model judge (prompt `verifier_v1`) that can add objections but never remove them.
* **Hand-off**: the proposal plus every passage retrieved. On failure, attempt 2 with the issues as hints, then
  escalate `verification_failed`. **Two agents, one deployable** (`worker`): they share in-memory state within a run; a
  separate container would add a network hop per run for no scaling benefit.
* **Failure mid-run**: provider down -> retries with backoff -> circuit breaker -> rule-engine fallback (run flagged
  `degraded`, still verified) -> or, with `LLM_FALLBACK=none`, escalate `provider_unavailable`. Limits -> escalate
  `limits_exceeded`. Worker dies -> heartbeat reaper re-queues the run (twice, then `failed`). No path returns an
  unverified or unfinished run as an answer.
* **Loop limits**: `MAX_STEPS=10`, `MAX_TOOL_CALLS=10`, `RUN_TIMEOUT_S=90`, `MAX_VERIFY_ATTEMPTS=2`, timeouts on every
  model call (`LLM_TIMEOUT_S`) and tool call (`TOOL_TIMEOUT_S`). The agentic path needs 7-8 steps on this corpus; 10 leaves
  room for one retry. A repeated identical tool call is answered with a "duplicate call" observation, and with two steps
  left the model is told to finish or escalate (both added after a real model looped, INC-003).

**The section 4.1 case** (`docs/eval1_reference_case.md`): a single search returns six SEBI SCORES passages and no RBI
clause; the deciding SEBI exclusion ranks 11th of 64. The agent (1) searches, (2) because the investor named SEBI, reads
SEBI's maintainability clauses and finds `SEBI-SCORES#AII-5.3` (RBI matters are not dealt through SCORES), (3) searches
the RBI framework with a query built from "bank" and "deposit", reads its scope (`3(1)(e)` bank, `3(1)(j)` Regulated
Entity, `9` grounds) and maintainability (`10(2)(a)` complain to the bank first), (4) verifies, and routes to the RBI
Ombudsman. With a real model (`gemma4`, 8B, local) the first answer cited only the bank definition; the Verifier rejected
it for missing the SEBI exclusion; the model searched SEBI's maintainability clauses, cited `AII-5.3`, and passed.

## 3. Architecture and deployment boundaries

```
 browser / curl --> api (FastAPI) --enqueue--> redis <--BRPOP-- worker x N (Investigator + Verifier) --> stub-llm | provider
                     validation, quarantine,    queue, runs,      FAISS index on shared volume <-- indexer (one-shot)
                     rate limit, run cache      traces, cache
 prometheus <- api, every worker replica (DNS discovery), stub-llm, cadvisor     grafana <- prometheus
 jaeger <- OTLP spans from api and worker (one trace per run, correlation id carried through the queue)
```

| Container | Why it is a separate deployment |
|---|---|
| `api` | Scaling/resource profile: cheap, I/O bound, must stay responsive while runs take seconds to minutes |
| `worker` | Scaling and failure isolation: scaled with `--scale`; a crash loses at most its in-flight runs, which are re-queued |
| `redis` | Failure isolation: durable hand-off between api and workers (append-only file) |
| `indexer` | Independent release, resource profile: the only heavy batch job; runs, publishes the index, exits |
| `stub-llm` | Failure isolation: the model is an external dependency; as a service, timeouts and the breaker are exercised for real |
| `prometheus`, `grafana`, `jaeger`, `cadvisor` | Observability, released independently |

Async with polling (A4): `POST` returns `202` and a run id in milliseconds; the caller polls `GET /v1/runs/{id}`. A
synchronous call would tie API capacity to model latency; callbacks need a reachable caller, which a browser is not.

## 4. Design decisions and what was rejected

| Decision | Why | Rejected |
|---|---|---|
| Whole-clause chunks with `kind` metadata and the regulation's own numbering | The deciding clause is a scope or exclusion clause the complaint does not resemble; `list_clauses kind=...` asks for it directly. Citations map to the PDF. | Fixed word windows: final accuracy 1.00 -> 0.15 (section 5) |
| Lead sentence repeated on each list item (SCORES Annex II 5.x) | "RBI matters are not dealt through SCORES" must be citable alone | Whole annexure as one chunk |
| MiniLM embeddings, local | Gold recall 0.917 -> 0.978 over the lexical embedder; free | Paid embedding API |
| Deterministic Verifier as the gate, model judge only adds objections | The model that can be wrong cannot be its own only check | LLM-only verification |
| Redis list + heartbeat reaper as the queue | ~40 lines, no extra moving parts | Celery/RQ |
| Rule engine as fallback and behind the stub | System runs with no key; load test measures the system | No fallback (available: `LLM_FALLBACK=none`) |
| Only the servicing provisions of the MF Regulations | 161 pages are mostly investment limits no grievance turns on | Indexing the whole document |
| `gemma4` 8B (16k context) as the local real model | Follows the protocol; works within a laptop | `llama3.2` 3B: confused action and route, looped (INC-003) |

## 5. Evaluation

**Set** (`eval/scenarios.json`): 34 scenarios, each with the expected route/regulator/domain (and timeline or escalation
reason where relevant) and the gold clauses that support it. Coverage: the 4.1 case and two variants (with a prompt
injection; with a prior unanswered complaint), 5 other RBI products, 12 SEBI entity cases across all five domains, 2
split-regulator cases, 3 out-of-corpus (insurance, crypto, pension), court-pending (SEBI and RBI), unlisted company,
one-year limitation, market loss (no provision decides it: must escalate), a vague complaint, abusive language, and 3
injected faults (fabricated citation, wrong timeline, invented provision). Run: `python -m eval.run_eval`.

**Metrics** (`eval/metrics.py`), lexical equivalents of the TruLens RAG triad, computed per claim of the determination's
`basis`; a claim is *supported* by text t if ≥70% of its content words occur in t and every number in it does:

| Metric | Computation |
|---|---|
| Groundedness | share of claims supported by the passage they cite (an unresolvable citation counts as unsupported) |
| Faithfulness | share of claims supported by any retrieved passage or the complaint |
| Hallucination rate | share of runs with at least one claim supported by nothing retrieved; also reported before verification |
| Context relevance | share of retrieved passages that are gold passages (precision), with gold recall alongside |

TruLens itself was not used: its feedback functions call a model per claim, which a free tier's rate limit and a laptop
model's speed make impractical over repeated runs. The lexical versions are free, deterministic and run in CI; they are
stricter than an LLM judge on paraphrase (a paraphrased true claim counts as unsupported), which is why the prompt asks for
verbatim claims.

### Results with a real model (gemma4 8B via Ollama, prompt v2, MiniLM, fallback disabled)

Stratified subset of 13 of the 34 scenarios (`--only S01 S02 S05 S06 S09 S12 S14 S16 S18 S20 S22 S24 S25`): a local
8B model needs ~4 min per scenario, so the full set twice would take ~5 hours on this laptop. Results:
`eval/real/20261112-222711.json`, log `eval/real/gemma4_v2_subset.log`. Settings: `LLM_REASONING_EFFORT=none`,
model judge on (the shipped default), `RUN_TIMEOUT_S=900`, `LLM_TIMEOUT_S=240`, no other load on the machine.

| Metric | gemma4, 13 scenarios |
|---|---|
| Accuracy (route, regulator, domain, timeline) | **0.46** (6/13) |
| Correct on the first attempt | 0.08 (1/13) |
| Groundedness / faithfulness | 1.00 / 1.00 |
| Hallucination rate (after / before verification) | 0.00 / 0.00 |
| Context relevance / gold recall | 0.20 / 0.68 |
| Verification failures caught | 9 (mean 1.7 attempts per run) |
| Cost per 100 runs (notional prices) | $1.34; largest driver `investigate`, 98% |

| Outcome | Scenarios |
|---|---|
| Correct | S01 (the 4.1 case, citing `SEBI-SCORES#AII-5.3`), S02, S05 (RBI), S06 (broker, 21 days), S14 (listed company), S22 (court: not maintainable) |
| Escalated instead (safe, not useful) | S09 MF, S12 registrar, S16 depository, S25 (4.1 + injection): `verification_failed` after two attempts; S20 insurance: escalated, labelled `entity_out_of_corpus` correctly but scored wrong only because the model wrote the string `"null"` as regulator (now normalised, like the RBI domain label) |
| **Wrong but grounded** | **S18** (UPI debit at the bank + MF units not allotted, two regulators): assigned to the mutual fund. **S24** (pure market loss): assigned to the mutual fund with a 21-day timeline |

Reading: the system never fabricated a citation (every claim of every answer resolved and was supported); when the model
could not assemble verifiable evidence it escalated, which is the F7 behaviour the brief asks for. Its weaknesses are the
two grounded-but-wrong answers: the Verifier checks that each cited clause says what is claimed and is of the right
kind, not that a second regulator is also involved (S18) or that the complaint describes a deficiency at all (S24). The
8B local model also needs a second attempt on most runs. A larger hosted model would be the first thing to try.

### L2: two prompt versions on the same set

`investigator_v1` (search once, answer from the top passages) vs `investigator_v2` (the five-step agentic procedure).
Rule-engine model, MiniLM, 34 scenarios (`--variants PROMPT_VERSION=v1 PROMPT_VERSION=v2`):

| Metric | v1 | v2 | Change |
|---|---|---|---|
| Correct on the first attempt | 0.176 | 0.912 | **+0.735** |
| Correct after verification | 0.971 | 1.000 | +0.029 |
| Gold recall | 0.952 | 0.978 | +0.027 |
| Verification attempts per run | 1.91 | 1.09 | -0.82 |
| Cost per 100 runs (notional) | $1.07 | $1.39 | +$0.32 |

**v2 is better on first-attempt accuracy by 0.735 (31 of 34 vs 6 of 34)**, at 30% more cost per run (more steps). The
Verifier rescues most of v1's wrong answers by rejecting them; on the retry the rule engine switches to the agentic
procedure, so v1 needs a second attempt on 91% of runs. The one v1 answer still wrong after verification shows the
Verifier's limit: for S24 (a pure market loss) v1 assigned the complaint to the mutual fund with a 21-day timeline; every
citation was true and of the right kind, so it passed. **Grounded is not the same as right.**

### L3: one deliberate change, before and after

Change: clause-level chunks -> fixed 180-word windows with 30-word overlap (`--variants CHUNKING=clause CHUNKING=fixed`),
everything else equal (rule engine, MiniLM, prompt v2):

| Metric | Before (clause) | After (fixed) |
|---|---|---|
| Accuracy (final) | 1.000 | 0.147 |
| Groundedness | 1.000 | 1.000 |
| Faithfulness | 1.000 | 1.000 |
| Hallucination rate | 0.000 | 0.000 |
| Hallucination rate before verification | 0.059 | 0.029 |
| Context relevance / gold recall | 0.158 / 0.978 | 0.000 / 0.000 |
| Cost per 100 runs (notional) | $1.39 | $1.06 |

Reading: with fixed windows the agent cannot ask a framework what it covers or excludes (windows have no `kind`), so it
cannot find the scope and servicing clauses and escalates `insufficient_evidence` on 29 of 34 scenarios. That is the safe
failure: groundedness and faithfulness stay at 1.0 and nothing is fabricated, but the service stops being useful. Context
relevance and gold recall read 0 partly by construction: the gold set is clause ids, which windows do not have. Accuracy is
the meaningful measure here.

Caveat on all rule-engine numbers: the rule engine and the scenarios were written by the same team, so accuracy near 1.0
proves the plumbing and the retrieval design, not model quality. The real-model table above is the quality measure.

## 6. Reliability, performance and cost

**SLO** (declared and committed in `ae4db28` before any load test): p95 of a complete run, submission to final result,
≤ 30 s at a sustained 1 run/s, no failed runs. A wrong-regulator routing delays redress by days; a determination in half
a minute is far faster than the manual first look it replaces. Full table: `docs/slo.md`.

| Workers | Max users meeting the SLO | Fails at | Throughput plateau |
|---|---|---|---|
| 1 | 8 (p95 14.0 s) | 16 users (p95 43 s) | 0.55 runs/s |
| 3 | 48 (p95 29.0 s) | 64 users (p95 39 s) | 1.64 runs/s |

**Bottleneck: worker concurrency slots.** Queue depth reached 31 while worker CPU was 0.03 cores and the stub answered in
under 1 s; run execution p95 was 11 s against 54 s end to end. Each run holds one of 4 threads for ~7.5 s while waiting on
~9 model calls. `--scale worker=3` therefore triples throughput. With a real provider the next limit is its rate limit
(1.6 runs/s ≈ 860 model calls/min).

**Caching (D7)**: whole-run cache keyed by normalised complaint fields + prompt version + index build; query-embedding
LRU. At 32 users with 3 workers, cache on: p95 19.0 s -> 1.2 s, cost per submission ~$0.015 -> ~$0.0001 (99% hits; an
upper bound, since the load test repeats 8 texts). **Rate limiting**: 30 submissions/min per client (fixed window in
Redis): one client looping against a 9-call agent could otherwise spend a budget in minutes.

**Cost** (L5; tokens and cost recorded at call time, per step and per run, in every trace and in Prometheus; notional
prices $0.0005 / $0.0015 per 1k prompt / completion tokens, a free tier costs $0):

| | Per run (4.1 case, stub) | Per 100 runs |
|---|---|---|
| Investigator (8 calls) | 26,003 + 1,007 tokens, $0.0145 | |
| Verifier judge (1 call) | 1,473 + 8 tokens, $0.0007 | |
| Total | $0.0153 | **$1.53** (eval set average: $1.39) |

**Largest cost driver: the Investigator's prompt tokens, ~95% of cost.** Each step re-sends every passage retrieved so far,
so prompt size grows with the step count. Options: send only new passages plus a digest, or a cheaper model for tool choice.

## 7. What did not work

* **A 3B model** (`llama3.2`) could not drive the tool protocol: it put route names in the action field, looped on one
  call, then copied the enum template literally. The loop bound and Verifier kept it safe (it escalated every time), not
  useful (INC-003).
* **Ollama's default 4k context** silently truncated prompts and produced empty replies; diagnosed from the trace (INC-004).
* **Output filter false positive**: "approach the Ombudsman *if it is rejected*" was blocked as a disposal statement, so
  every RBI referral escalated until conditionals were exempted (INC-005).
* **Grounded but wrong**: the Verifier checks that citations are true and of the right kind, not that they are the best
  evidence (INC-006) or the right route (the v1 cases in section 5).
* **Keyword domain classifier** in the rule engine is brittle by nature; it serves only as fallback and stub.
* **cAdvisor** cannot name containers on Docker Desktop with Docker 29; per-container CPU and memory on the dashboard come from
  each service's own process metrics there (cAdvisor works on a Linux host).
* **Market-loss complaints**: no indexed provision decides them, so they escalate. That is correct for this corpus, and
  a reminder that the system is only as complete as the documents indexed.
* **TruLens** was not used (section 5).
* **Real-model quality is modest**: 6 of 13 on the subset with an 8B local model, two of the misses grounded but wrong
  (section 5). The first two full real-model runs were lost to a tripped breaker under load (INC-011) and to the time a
  5-hour run would take; the subset is what was measured.
