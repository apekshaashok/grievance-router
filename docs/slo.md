# Service level objective (D6)

> Declared and committed on 2026-11-12, before any load test was run. The git history of this file is the evidence;
> the results table below was filled in afterwards and the target was not changed.

## Declared SLO

**p95 of a complete run, measured from submission to final result (queue wait included), <= 30 s, at a sustained 1 run/s,
with zero failed runs.** Set via `SLO_P95_SECONDS=30` (also drawn on the Grafana latency panel and checked by `load/locustfile.py`).

## Justification against section 1

A wrong-regulator routing delays redress; the service is the first triage step in front of a grievance officer, whose own
turnaround is hours to days. A determination within half a minute is therefore far faster than the step it replaces, and still
fast enough for the web client to feel interactive. Tighter (e.g. 5 s) would be unrealistic for a multi-step agent with a real
provider (~7 sequential model calls); looser (minutes) would remove the benefit over the manual first look.

Back-of-envelope for the stub (`STUB_DELAY_MS=800`): one run on the real corpus is ~8 Investigator calls + 1 Verifier call
= ~9 x 0.8 s = ~7 s of waiting plus retrieval and overhead. One worker with 4 threads sustains ~4 / 7.5 = ~0.5 runs/s;
3 workers ~1.6 runs/s. So 1 run/s is feasible with 2-3 workers and infeasible with 1, which is exactly what
`--scale` should show.

## How to measure
```
docker compose up -d --scale worker=1      # then repeat with =3
docker compose --profile load up locust    # http://localhost:8089 : users 5,10,20,40,... spawn rate 5
```
The Locust run reports `submission_to_result` p50/p95 and prints `SLO CHECK ... MET / NOT MET`. Raise the user count until it
fails, note the concurrency, then read the Grafana panels (CPU per container, queue depth, per-step p95) to name the component that
saturates first (expected with the stub: worker threads; with a real provider: its rate limit).
For D7: run once with `Cache-Control: no-cache` removed from the Locust headers (cache on) and once with it (cache off) and compare p95 and cost.

## Results (measured 2026-11-12, after the SLO above was committed in `ae4db28`)

Setup: `docker compose` on one laptop (Apple Silicon, 10 cores, Docker Desktop), model = `stub-llm` (`STUB_DELAY_MS=800`,
uniform +/-20%), `WORKER_CONCURRENCY=4` threads per worker, MiniLM embeddings, prompt v2, Locust closed loop (each user submits,
polls every 0.5 s until the run finishes, waits 1-3 s, repeats), 2 minutes per point, `Cache-Control: no-cache` unless stated.
Reproduce: `scripts/loadtest.sh <users> 2m <label>` then `python3 scripts/summarise_load.py`. Raw CSVs: `load/results/`.

| run | users | runs | failed | runs/s | p50 (s) | p95 (s) | p95 <= 30s? |
|---|---|---|---|---|---|---|---|
| w1 | 4 | 50 | 0 | 0.42 | 7.6 | 8.2 | yes |
| w1 | 8 | 66 | 0 | 0.55 | 12.0 | 14.0 | yes |
| w1 | 16 | 66 | 0 | 0.49 | 26.0 | 43.0 | NO |
| w1 | 24 | 58 | 0 | 0.49 | 40.0 | 57.0 | NO |
| w3 | 8 | 99 | 0 | 0.83 | 7.6 | 8.6 | yes |
| w3 | 16 | 193 | 0 | 1.62 | 7.6 | 9.2 | yes |
| w3 | 32 | 196 | 0 | 1.64 | 17.0 | 19.0 | yes |
| w3 | 48 | 191 | 0 | 1.60 | 26.0 | 29.0 | yes |
| w3 | 64 | 195 | 0 | 1.63 | 35.0 | 39.0 | NO |
| w3cache | 32 | 1012 | 0 | 8.49 | 0.5 | 1.2 | yes |

(`w1` = `--scale worker=1`, `w3` = `--scale worker=3`, `w3cache` = 3 workers with the whole-run cache allowed.)

**Target met?** Yes up to 8 concurrent users with one worker (0.55 runs/s), and up to 48 with three (1.6 runs/s). It fails
at **16 users with one worker** (p95 43 s) and at **64 users with three** (p95 39 s). The sustained 1 run/s of the SLO needs
at least two workers; one worker cannot reach it at any concurrency (plateau 0.55 runs/s).

**Bottleneck: worker concurrency slots, not CPU and not the model.** During the one-worker 16- and 24-user runs, Prometheus
showed queue depth up to 31, worker CPU at most 0.03 cores, stub p95 latency 0.98 s, and run execution p95 11 s against
submission-to-result p95 54 s: runs spent most of their time waiting in the Redis queue for one of the worker's 4 threads.
Each run holds a thread for ~7.5 s while it waits on ~9 sequential model calls, so capacity is
`workers x WORKER_CONCURRENCY / 7.5 s` = 0.53 runs/s for one worker, 1.6 for three, matching the plateaus measured.

**Effect of `--scale worker=3`:** throughput x3.0 (0.55 -> 1.64 runs/s); at 16 users p95 fell from 43 s to 9.2 s. Scaling helps
because the constraint is slots per replica. With a real provider the next limit would be the provider's rate limit (requests
or tokens per minute): at ~9 calls per run, 1.6 runs/s is ~860 model calls per minute, above most free tiers.
(Raising `WORKER_CONCURRENCY` would add slots without new containers; replicas were used because A7 asks for `--scale`, and
they also isolate failures.)

**Effect of caching (D7), 3 workers, 32 users:** with the whole-run cache allowed, p95 fell from 19.0 s to 1.2 s and throughput
rose from 1.6 to 8.5 runs/s; 995 of 1,004 submissions were cache hits, so only 9 runs called the model: cost per submission fell
from ~$0.015 to ~$0.0001 (notional prices, below). This is an upper bound: the load test cycles through 8 complaint texts.
Real traffic repeats far less (double submits, retries, copied complaints), and the saving scales with the duplicate rate.
The query-embedding cache served 861 of 861 lookups in the same window (agents re-issue the same derived queries).
