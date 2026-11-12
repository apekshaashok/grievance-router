"""Load test (D6). Measures SUBMISSION -> FINAL RESULT, not the submission call alone.

Run against a stack whose LLM is the stub service (LLM_BASE_URL -> stub-llm), so the test measures this system, not a
provider's rate limits.   docker compose --profile load up locust   ->  http://localhost:8089

The p95 target is read from SLO_P95_SECONDS; it must be declared in docs/slo.md and committed BEFORE running.
At the end the test prints whether the target was met and exits non-zero if it was not.
"""
import os
import random
import time
import uuid

from locust import HttpUser, between, events, task

SLO = float(os.environ.get("SLO_P95_SECONDS", "30"))
POLL_EVERY_S = 0.5
GIVE_UP_S = 180

COMPLAINTS = [
    "I invested in a fixed deposit through my bank's app. The interest credited is lower than what I was promised. I want to complain to SEBI.",
    "My stock broker executed unauthorised trades in my trading account while I was abroad and I lost money on them.",
    "I requested redemption of my mutual fund units a month ago but the proceeds have still not reached me.",
    "The listed company declared a dividend last year but I have not received my dividend payment.",
    "My demat account was frozen by the depository participant without any notice and I cannot sell my holdings.",
    "A UPI payment of 5000 rupees failed but the amount was debited from my account and was not returned for ten days.",
    "The registrar has not completed the share transfer even though I sent all the documents months ago.",
    "My insurance company has rejected my claim settlement even though all premiums were paid on time.",
]


class Investor(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.client_id = f"load-{uuid.uuid4().hex[:8]}"   # distinct client so rate limiting does not mask the result

    @task
    def complaint_to_result(self):
        body = {"investor_ref": f"INV{random.randint(100000, 999999)}", "complaint_text": random.choice(COMPLAINTS),
                "channel": "web", "complaint_date": time.strftime("%Y-%m-%d")}
        headers = {"X-Client-Id": self.client_id, "Cache-Control": "no-cache"}   # no-cache: measure real runs, not cache hits
        t0 = time.time()
        with self.client.post("/v1/complaints", json=body, headers=headers, name="POST /v1/complaints", catch_response=True) as r:
            if r.status_code != 202:
                r.failure(f"submit failed: {r.status_code}")
                return
            run_id = r.json()["run_id"]
        status = "timeout"
        while time.time() - t0 < GIVE_UP_S:
            time.sleep(POLL_EVERY_S)
            with self.client.get(f"/v1/runs/{run_id}", name="GET /v1/runs/{id}", catch_response=True) as r:
                status = r.json().get("status") if r.status_code == 200 else "error"
            if status in ("completed", "failed"):
                break
        ms = (time.time() - t0) * 1000
        events.request.fire(request_type="E2E", name="submission_to_result", response_time=ms, response_length=0,
                            exception=None if status == "completed" else Exception(f"run {status}"), context={})


@events.quitting.add_listener
def _check_slo(environment, **_):
    s = environment.stats.get("submission_to_result", "E2E")
    if s.num_requests == 0:
        return
    p50, p95 = s.get_response_time_percentile(0.5) / 1000, s.get_response_time_percentile(0.95) / 1000
    rps = s.total_rps if hasattr(s, "total_rps") else s.num_requests / max(s.last_request_timestamp - s.start_time, 1)
    met = p95 <= SLO and s.num_failures == 0
    print(f"\n=== SLO CHECK: runs={s.num_requests} failures={s.num_failures} p50={p50:.1f}s p95={p95:.1f}s target_p95={SLO:.0f}s "
          f"throughput={rps:.2f} runs/s -> {'MET' if met else 'NOT MET'} ===")
    if not met:
        environment.process_exit_code = 1
