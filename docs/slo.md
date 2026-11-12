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

## Results (fill in from YOUR runs)

| workers | users | RPS | p50 | p95 | target met? | bottleneck |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 3 | | | | | | |
