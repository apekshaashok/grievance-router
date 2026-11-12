"""Redis-backed state: queue, run records, traces, quarantine, feedback, cache, rate limit, quality samples."""
import hashlib
import json
import time
import uuid
from typing import Any, List, Optional

import redis

from .config import cfg


def connect():
    return redis.Redis.from_url(cfg.require("REDIS_URL"), decode_responses=True)


def _j(x):
    return json.dumps(x, default=str)


class Store:
    def __init__(self, r=None):
        self.r = r or connect()

    # ---- queue ----
    def enqueue(self, msg: dict):
        self.r.lpush(cfg.QUEUE_NAME, _j(msg))

    def pop(self, timeout: int = 2) -> Optional[dict]:
        item = self.r.brpop(cfg.QUEUE_NAME, timeout=timeout)
        return json.loads(item[1]) if item else None

    def depth(self) -> int:
        return int(self.r.llen(cfg.QUEUE_NAME))

    # ---- runs ----
    def create_run(self, run_id, payload, client, correlation_id, flags, status="queued", result=None, cache_hit=False):
        now = time.time()
        rec = {"run_id": run_id, "status": status, "payload": _j(payload), "client": client,
               "correlation_id": correlation_id, "submitted_at": now, "flags": _j(flags), "cache_hit": int(cache_hit),
               "starts": 0}
        if result is not None:
            rec["result"] = _j(result)
            rec["completed_at"] = now
        self.r.hset(f"run:{run_id}", mapping=rec)
        self.r.expire(f"run:{run_id}", 7 * 86400)

    def get_run(self, run_id) -> Optional[dict]:
        h = self.r.hgetall(f"run:{run_id}")
        if not h:
            return None
        for k in ("payload", "result", "flags"):
            if k in h:
                h[k] = json.loads(h[k])
        for k in ("submitted_at", "started_at", "completed_at", "heartbeat"):
            if k in h:
                h[k] = float(h[k])
        return h

    def mark_running(self, run_id):
        now = time.time()
        self.r.hset(f"run:{run_id}", mapping={"status": "running", "started_at": now, "heartbeat": now})
        self.r.hincrby(f"run:{run_id}", "starts", 1)
        self.r.sadd("runs:running", run_id)

    def heartbeat(self, run_id):
        self.r.hset(f"run:{run_id}", "heartbeat", time.time())

    def finish_run(self, run_id, status, result: dict):
        self.r.hset(f"run:{run_id}", mapping={"status": status, "result": _j(result), "completed_at": time.time()})
        self.r.srem("runs:running", run_id)

    def save_trace(self, run_id, events: List[dict]):
        self.r.set(f"trace:{run_id}", _j(events), ex=7 * 86400)

    def get_trace(self, run_id) -> Optional[list]:
        raw = self.r.get(f"trace:{run_id}")
        return json.loads(raw) if raw else None

    def requeue_stale(self, stale_s: float, max_starts: int = 2) -> List[str]:
        """Recover runs whose worker died mid-run (heartbeat stopped). Guarded by a lock so replicas do not double-reap."""
        if not self.r.set("reaper:lock", "1", nx=True, ex=10):
            return []
        recovered = []
        for run_id in self.r.smembers("runs:running"):
            h = self.r.hgetall(f"run:{run_id}")
            if not h:
                self.r.srem("runs:running", run_id)
                continue
            if h.get("status") != "running" or time.time() - float(h.get("heartbeat", 0)) < stale_s:
                continue
            if int(h.get("starts", 1)) < max_starts:
                self.r.hset(f"run:{run_id}", "status", "queued")
                self.r.srem("runs:running", run_id)
                self.enqueue({"run_id": run_id, "correlation_id": h.get("correlation_id"), "submitted_at": float(h["submitted_at"])})
                recovered.append(f"requeued:{run_id}")
            else:
                self.finish_run(run_id, "failed", {"error": "worker_lost", "message": "worker stopped responding twice"})
                recovered.append(f"failed:{run_id}")
        return recovered

    # ---- quarantine (D1) ----
    def quarantine(self, record: Any, errors: List[dict], client: str, correlation_id: str) -> str:
        qid = uuid.uuid4().hex[:12]
        self.r.lpush("quarantine", _j({"quarantine_id": qid, "at": time.time(), "client": client,
                                        "correlation_id": correlation_id, "record": record, "errors": errors}))
        self.r.ltrim("quarantine", 0, 4999)
        return qid

    def list_quarantine(self, limit=50) -> list:
        return [json.loads(x) for x in self.r.lrange("quarantine", 0, limit - 1)]

    # ---- feedback (L6) ----
    def add_feedback(self, run_id, satisfactory: bool, reason: Optional[str]):
        self.r.lpush("feedback", _j({"run_id": run_id, "satisfactory": satisfactory, "reason": reason, "at": time.time()}))
        self.r.hincrby("feedback:counts", "satisfied" if satisfactory else "unsatisfied", 1)

    def feedback_summary(self) -> dict:
        c = self.r.hgetall("feedback:counts")
        sat, uns = int(c.get("satisfied", 0)), int(c.get("unsatisfied", 0))
        reasons = {}
        for x in self.r.lrange("feedback", 0, 499):
            f = json.loads(x)
            if not f["satisfactory"] and f.get("reason"):
                reasons[f["reason"]] = reasons.get(f["reason"], 0) + 1
        return {"total": sat + uns, "satisfied": sat, "unsatisfied": uns,
                "satisfaction_rate": (sat / (sat + uns)) if sat + uns else None,
                "top_reasons": sorted(reasons.items(), key=lambda kv: -kv[1])[:10]}

    # ---- quality samples (L6) ----
    def add_quality(self, sample: dict):
        self.r.lpush("quality", _j(sample))
        self.r.ltrim("quality", 0, 999)

    def list_quality(self, limit=200) -> list:
        return [json.loads(x) for x in self.r.lrange("quality", 0, limit - 1)][::-1]

    # ---- cache (D7) ----
    @staticmethod
    def cache_key(fields: dict, prompt_version: str, index_built_at: str) -> str:
        norm = {k: (v.strip().lower() if isinstance(v, str) else v) for k, v in fields.items()
                if k not in ("investor_ref", "channel")}   # complaint_date stays: limitation rules depend on it
        raw = _j({"f": norm, "p": prompt_version, "i": index_built_at})
        return "cache:run:" + hashlib.sha256(raw.encode()).hexdigest()[:32]

    def cache_get(self, key) -> Optional[dict]:
        raw = self.r.get(key)
        return json.loads(raw) if raw else None

    def cache_set(self, key, result: dict):
        self.r.set(key, _j(result), ex=cfg.CACHE_TTL_S)

    # ---- rate limit (D7): fixed window per client ----
    def rate_limit(self, client: str, limit: int, window_s: int = 60):
        bucket = int(time.time() // window_s)
        key = f"rl:{client}:{bucket}"
        n = self.r.incr(key)
        if n == 1:
            self.r.expire(key, window_s + 5)
        return n <= limit, window_s - int(time.time() % window_s)

    def ping(self) -> bool:
        return bool(self.r.ping())
