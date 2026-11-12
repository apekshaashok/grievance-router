"""Batch evaluation from the command line (L2, L3).

  python -m eval.run_eval                                   # run the evaluation set once
  python -m eval.run_eval --variants PROMPT_VERSION=v1 PROMPT_VERSION=v2          # compare two prompt variants (L2)
  python -m eval.run_eval --variants CHUNKING=clause CHUNKING=fixed               # one deliberate change, before/after (L3)
  python -m eval.run_eval --variants "A=1,B=2" ...         # several settings per variant; each variant re-ingests the corpus
  python -m eval.run_eval --fail-under 0.9                  # exit 1 if final accuracy is below 0.9 (CI gate)

With LLM_PROVIDER=rules (default) the model is the deterministic rule engine, so results are exactly repeatable and
need no key. For the numbers in your report run once with a real provider configured in .env.
"""
import argparse
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import ingest  # noqa: E402
from app.agents.graph import run_agent  # noqa: E402
from app.config import cfg  # noqa: E402
from app.llm.resilience import build_llm  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.retrieval import index as index_mod  # noqa: E402
from eval.metrics import claim_metrics, mean, outcome_correct, retrieval_metrics  # noqa: E402


def run_variant(label, overrides, scenarios, verbose=False):
    saved = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    os.environ["ENABLE_FAULT_INJECTION"] = "1"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ingest.build(index_dir=tmp)
            index_mod.clear_cache()
            idx = index_mod.load_index(tmp)
            llm = build_llm()
            rows = []
            for sc in scenarios:
                t0 = time.time()
                r = run_agent(sc["fields"], run_id=f"eval-{sc['id']}", fault=sc.get("fault"), llm=llm, index=idx)
                text = {k: v["text"] for k, v in r["_retrieved"].items()}
                first = r["first_proposal"]
                row = {
                    "id": sc["id"], "description": sc["description"], "expected": sc["expected"],
                    "route": r["determination"]["route"], "regulator": r["determination"].get("regulator"),
                    "domain": r["determination"].get("domain"),
                    "escalation_reason": (r["determination"].get("escalation") or {}).get("reason"),
                    "correct": outcome_correct(r["determination"], sc["expected"]),
                    "first_attempt_correct": outcome_correct(first, sc["expected"]) and not sc.get("fault"),
                    "verification_caught_error": bool(r["issues_history"]), "attempts": r["attempts"],
                    "steps": r["steps"], "tool_calls": r["tool_calls"], "exec_s": round(time.time() - t0, 3),
                    "degraded": r["degraded"], "tokens": r["usage"]["total"]["prompt_tokens"] + r["usage"]["total"]["completion_tokens"],
                    "cost_usd": r["usage"]["total"]["cost_usd"], "by_step": r["usage"]["by_step"],
                    "final": claim_metrics(r["determination"], text, sc["fields"]),
                    "before_verification": claim_metrics(first, text, sc["fields"]),
                    "retrieval": retrieval_metrics(r["retrieved_ids"], sc["gold_clauses"]),
                    "retrieved_ids": r["retrieved_ids"], "gold_clauses": sc["gold_clauses"],
                    "guardrail_injection": r["guardrails"]["injection_detected"],
                    "prompt_version": r["prompt_version"], "summary": r["determination"]["summary"],
                    "citations": [c["id"] for c in r["citations"]],
                }
                rows.append(row)
                if verbose:
                    print(f"  {'OK ' if row['correct'] else 'BAD'} {sc['id']:30} {row['route']:26} {row['regulator'] or '-':5} "
                          f"steps={row['steps']} attempts={row['attempts']}")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return summarise(label, overrides, rows)


def summarise(label, overrides, rows):
    n = len(rows)
    cost = sum(r["cost_usd"] for r in rows)
    tokens_by_step, cost_by_step = {}, {}
    for r in rows:
        for step, u in r["by_step"].items():
            tokens_by_step[step] = tokens_by_step.get(step, 0) + u["prompt_tokens"] + u["completion_tokens"]
            cost_by_step[step] = cost_by_step.get(step, 0.0) + u["cost_usd"]
    drivers = cost_by_step if cost > 0 else tokens_by_step
    top = max(drivers.items(), key=lambda kv: kv[1]) if drivers else (None, 0)
    return {
        "label": label, "overrides": overrides, "n": n,
        "metrics": {
            "accuracy_final": sum(r["correct"] for r in rows) / n,
            "accuracy_first_attempt": sum(r["first_attempt_correct"] for r in rows) / n,
            "groundedness": mean([r["final"]["groundedness"] for r in rows]),
            "context_relevance": mean([r["retrieval"]["context_relevance"] for r in rows]),
            "gold_recall": mean([r["retrieval"]["gold_recall"] for r in rows]),
            "faithfulness": mean([r["final"]["faithfulness"] for r in rows]),
            "hallucination_rate": sum(r["final"]["hallucinated"] for r in rows) / n,
            "hallucination_rate_before_verification": sum(r["before_verification"]["hallucinated"] for r in rows) / n,
            "verification_caught": sum(r["verification_caught_error"] for r in rows),
            "mean_steps": mean([r["steps"] for r in rows]), "mean_tool_calls": mean([r["tool_calls"] for r in rows]),
            "mean_attempts": mean([r["attempts"] for r in rows]),
            "cost_per_100_runs_usd": cost / n * 100, "tokens_per_100_runs": sum(r["tokens"] for r in rows) / n * 100,
            "largest_cost_driver": top[0], "largest_cost_driver_share": (top[1] / sum(drivers.values())) if drivers and sum(drivers.values()) else None,
        },
        "rows": rows,
    }


def fmt(v):
    return "-" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))


def print_summary(results):
    keys = list(results[0]["metrics"].keys())
    w = max(len(k) for k in keys) + 2
    print("\n" + "metric".ljust(w) + "".join(r["label"][:28].ljust(30) for r in results))
    for k in keys:
        print(k.ljust(w) + "".join(fmt(r["metrics"][k]).ljust(30) for r in results))
    if len(results) == 2:
        a, b = results[0]["metrics"], results[1]["metrics"]
        print("\ndelta (second - first):")
        for k in keys:
            if isinstance(a[k], (int, float)) and isinstance(b[k], (int, float)):
                print(f"  {k.ljust(w)}{b[k] - a[k]:+.3f}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", default=os.path.join(os.path.dirname(__file__), "scenarios.json"))
    ap.add_argument("--variants", nargs="*", default=[""], help='each "K=V,K2=V2"; empty string = current settings')
    ap.add_argument("--only", nargs="*"), ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--fail-under", type=float), ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "results"))
    a = ap.parse_args(argv)
    setup_logging()
    import logging
    logging.getLogger().setLevel(logging.ERROR)
    scenarios = json.load(open(a.scenarios))
    if a.only:
        scenarios = [s for s in scenarios if s["id"] in a.only or any(s["id"].startswith(o) for o in a.only)]
    results = []
    for v in a.variants:
        overrides = dict(kv.split("=", 1) for kv in v.split(",") if kv)
        label = v or "baseline"
        print(f"== variant: {label}  ({len(scenarios)} scenarios)")
        results.append(run_variant(label, overrides, scenarios, a.verbose))
    print_summary(results)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, time.strftime("%Y%m%d-%H%M%S") + ".json")
    json.dump(results, open(path, "w"), indent=1, default=str)
    print(f"\nfull per-scenario results: {path}")
    bad = [r for r in results[0]["rows"] if not r["correct"]]
    if bad:
        print("\nincorrect in first variant:", ", ".join(r["id"] for r in bad))
    if a.fail_under is not None and results[-1]["metrics"]["accuracy_final"] < a.fail_under:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
