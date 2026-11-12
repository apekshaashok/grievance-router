from dataclasses import dataclass
from typing import Protocol


class ProviderError(Exception):
    """The provider failed (timeout, 5xx, connection error). Counts toward the circuit breaker."""


class ProviderUnavailable(Exception):
    """No provider could serve the call (breaker open and no fallback, or retries exhausted)."""


@dataclass
class LLMResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    provider: str
    model: str = ""
    degraded: bool = False
    approx_tokens: bool = False


class Provider(Protocol):
    name: str

    def complete(self, system: str, user: str, step: str, timeout: float) -> LLMResult: ...
