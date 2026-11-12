"""Timeouts, bounded retry with exponential backoff, circuit breaker, defined fallback path (A5).

Fallback path:  primary provider  ->  (retries, then breaker opens)  ->  offline rules provider (degraded=True)
                -> if that cannot produce a verified answer, the run escalates to a human.
A run is never returned as a confident answer after a failure it did not recover from.
"""
import logging
import random
import threading
import time

from ..config import cfg
from ..logging_setup import log
from ..metrics import BREAKER_OPEN, LLM_CALLS, RETRIES
from .base import LLMResult, ProviderError, ProviderUnavailable

logger = logging.getLogger("llm")


class CircuitBreaker:
    CLOSED, OPEN, HALF = "closed", "open", "half-open"

    def __init__(self, failures: int, cooldown: float):
        self.threshold, self.cooldown = failures, cooldown
        self.state, self.fails, self.opened_at = self.CLOSED, 0, 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self.state == self.OPEN:
                if time.time() - self.opened_at >= self.cooldown:
                    self.state = self.HALF  # let one trial call through
                    return True
                return False
            return True

    def success(self):
        with self._lock:
            self.fails, self.state = 0, self.CLOSED
            BREAKER_OPEN.set(0)

    def failure(self):
        with self._lock:
            self.fails += 1
            if self.state == self.HALF or self.fails >= self.threshold:
                if self.state != self.OPEN:
                    log(logger, "circuit breaker opened", level="warning", consecutive_failures=self.fails)
                self.state, self.opened_at = self.OPEN, time.time()
                BREAKER_OPEN.set(1)


class ResilientLLM:
    def __init__(self, primary, fallback=None, breaker: CircuitBreaker | None = None):
        self.primary, self.fallback = primary, fallback
        self.breaker = breaker or CircuitBreaker(cfg.BREAKER_FAILURES, cfg.BREAKER_COOLDOWN_S)

    def complete(self, system: str, user: str, step: str, deadline: float | None = None) -> LLMResult:
        """`deadline` (epoch seconds) is the run's wall-clock limit: each attempt gets at most the time left, and no
        retry starts after it, so one slow call cannot overrun RUN_TIMEOUT_S by several timeouts."""
        if self.primary is not None and self.breaker.allow():
            attempts = cfg.LLM_MAX_RETRIES + 1
            for i in range(attempts):
                timeout = cfg.LLM_TIMEOUT_S if deadline is None else min(cfg.LLM_TIMEOUT_S, deadline - time.time())
                if timeout <= 1:
                    raise ProviderUnavailable("run deadline reached before the model call could be made")
                try:
                    res = self.primary.complete(system, user, step, timeout)
                    self.breaker.success()
                    LLM_CALLS.labels(self.primary.name, "ok").inc()
                    return res
                except ProviderError as exc:
                    LLM_CALLS.labels(self.primary.name, "error").inc()
                    self.breaker.failure()
                    log(logger, "llm call failed", level="warning", step=step, attempt=i + 1, error=str(exc)[:200])
                    if self.breaker.state == CircuitBreaker.OPEN or i == attempts - 1:
                        break
                    RETRIES.labels("llm").inc()
                    time.sleep(cfg.LLM_BACKOFF_BASE_S * (2 ** i) * (0.75 + random.random() / 2))  # exponential + jitter
        else:
            LLM_CALLS.labels(getattr(self.primary, "name", "none"), "short_circuit").inc()
        return self.fallback_complete(system, user, step)

    def fallback_complete(self, system: str, user: str, step: str) -> LLMResult:
        if self.fallback is None:
            raise ProviderUnavailable("model provider unavailable and no fallback configured")
        res = self.fallback.complete(system, user, step, cfg.LLM_TIMEOUT_S)
        LLM_CALLS.labels(self.fallback.name, "fallback").inc()
        res.degraded = True
        return res


def build_llm() -> ResilientLLM:
    from .rules import RulesProvider
    fallback = RulesProvider() if cfg.LLM_FALLBACK == "rules" else None
    if cfg.LLM_PROVIDER == "rules":
        return ResilientLLM(RulesProvider(), None)
    if cfg.LLM_PROVIDER == "openai_compat":
        from .openai_compat import OpenAICompatProvider
        return ResilientLLM(OpenAICompatProvider(), fallback)
    raise RuntimeError(f"unknown LLM_PROVIDER {cfg.LLM_PROVIDER!r}")


_shared = None
_shared_lock = threading.Lock()


def shared_llm() -> ResilientLLM:
    """One breaker per process, shared by all worker threads."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = build_llm()
        return _shared
