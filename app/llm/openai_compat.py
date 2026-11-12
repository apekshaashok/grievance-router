"""Any OpenAI-compatible /chat/completions endpoint: provider free tiers, Ollama, or the local stub.
The model sits behind this interface so the provider can be changed by configuration only (cost note)."""
import httpx

from ..config import cfg
from .base import LLMResult, ProviderError


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self):
        self.base = cfg.require("LLM_BASE_URL").rstrip("/")
        self.model = cfg.LLM_MODEL or "default"
        self.key = cfg.LLM_API_KEY
        self.client = httpx.Client()

    def complete(self, system, user, step, timeout):
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        body = {"model": self.model, "temperature": 0, "max_tokens": cfg.LLM_MAX_TOKENS,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            r = self.client.post(f"{self.base}/chat/completions", json=body, headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderError(f"HTTP {r.status_code}")
        if r.status_code >= 400:
            raise ProviderError(f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
            text = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise ProviderError(f"malformed provider response: {exc}") from exc
        usage = data.get("usage") or {}
        approx = "prompt_tokens" not in usage
        return LLMResult(text=text,
                         prompt_tokens=usage.get("prompt_tokens") or (len(system) + len(user)) // 4,
                         completion_tokens=usage.get("completion_tokens") or len(text) // 4,
                         provider=self.name, model=self.model, approx_tokens=approx)
