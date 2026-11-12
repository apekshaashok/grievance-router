import json

from ..agents.policy import decide
from .base import LLMResult


class RulesProvider:
    """Offline provider: deterministic rule engine behind the same interface as a real model."""
    name = "rules"

    def complete(self, system, user, step, timeout=None):
        out = json.dumps(decide(system, json.loads(user)))
        return LLMResult(text=out, prompt_tokens=(len(system) + len(user)) // 4, completion_tokens=len(out) // 4,
                         provider=self.name, model="rules-v1", approx_tokens=True)
