# Report - Mini Project 12: Securities Investor Grievance Routing

> Plain report, brief. Fill every section with YOUR results; delete these hints. Say what did not work.

## 1. Problem and scope
(What the service does and does not do; never disposes of a complaint; synthetic data only; corpus used - real documents with versions.)

## 2. Agent design
* Topology (Investigator -> Verifier/Router), hand-off, what happens if one fails mid-run: see `docs/architecture.md`.
* Tools (4), verification rules, loop limits (`MAX_STEPS`, `MAX_TOOL_CALLS`, `RUN_TIMEOUT_S`) and why those values.
* The section 4.1 case, step by step, with the passages it depends on.

## 3. Architecture diagram (deployment boundaries marked)
(Use the diagram in `docs/architecture.md`; justify each boundary on scaling / failure isolation / independent release / resource profile.)

## 4. Design decisions and what was rejected
(`docs/architecture.md`, "Alternatives rejected": add anything you actually tried and dropped.)

## 5. Evaluation (before and after)
* Eval set: N scenarios, how built, expected outcomes, supporting passages.
* L2: prompt v1 vs v2 on the same set - which metric, which won, by how much.
* L3: the one deliberate change, and the table groundedness / context relevance / faithfulness / hallucination rate before and after.
* How each metric is computed (`eval/metrics.py`); state that they are lexical equivalents of TruLens measures (or what you used).
* Which model produced these numbers (real model, not the rule engine).

## 6. Reliability and cost
* Declared p95 SLO (and commit hash/date showing it preceded measurement), measured RPS / p50 / p95, met or not, concurrency where it fails, the bottleneck component.
* Effect of `--scale worker=3` on throughput and p95 (or why it did not help).
* Effect of caching on p95 and cost per run. Rate-limiting rationale.
* Cost per 100 runs and the largest single driver (per-step table).

## 7. What did not work
(Incidents from `docs/incident_log.md`, wrong turns, known limits: keyword classifier brittleness, sample vs real corpus, grounded-but-wrong answers, free-tier rate limits.)
