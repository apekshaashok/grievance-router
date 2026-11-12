"""Runtime configuration (D4).

Every environment-specific value is read from environment variables at access
time. Nothing here is a credential, endpoint or model identifier: those exist
only in a runtime .env (see .env.example).
"""
import os

# name: (cast, default).  default None => must be supplied where it is used.
_SPEC = {
    "REDIS_URL": (str, None),
    "INDEX_DIR": (str, "data/index"),
    "CORPUS_DIR": (str, "corpus"),
    "EMBEDDER": (str, "hash"),            # hash | sentence-transformers
    "EMBED_MODEL": (str, None),           # only for sentence-transformers
    "EMBED_DIM": (int, 512),              # only for hash embedder
    "CHUNKING": (str, "clause"),          # clause | fixed
    "CHUNK_HEADERS": (int, 1),            # 1 = prepend document+heading to embedded text
    "CHUNK_WORDS": (int, 180),
    "CHUNK_OVERLAP": (int, 30),
    "TOP_K": (int, 4),
    "LLM_PROVIDER": (str, "rules"),       # rules | openai_compat
    "LLM_BASE_URL": (str, None),
    "LLM_API_KEY": (str, None),
    "LLM_MODEL": (str, None),
    "LLM_FALLBACK": (str, "rules"),       # rules | none
    "LLM_TIMEOUT_S": (float, 30.0),
    "LLM_MAX_TOKENS": (int, 2000),
    "LLM_MAX_RETRIES": (int, 2),
    "LLM_BACKOFF_BASE_S": (float, 0.5),
    "BREAKER_FAILURES": (int, 3),
    "BREAKER_COOLDOWN_S": (float, 20.0),
    "TOOL_TIMEOUT_S": (float, 10.0),
    "MAX_STEPS": (int, 10),
    "MAX_TOOL_CALLS": (int, 10),
    "RUN_TIMEOUT_S": (float, 90.0),
    "MAX_VERIFY_ATTEMPTS": (int, 2),
    "VERIFY_WITH_LLM": (int, 1),
    "PROMPT_VERSION": (str, "v2"),
    "PROMPTS_DIR": (str, "prompts"),
    "PRICE_PROMPT_PER_1K": (float, 0.0),
    "PRICE_COMPLETION_PER_1K": (float, 0.0),
    "CACHE_TTL_S": (int, 3600),
    "RATE_LIMIT_PER_MIN": (int, 30),
    "WORKER_CONCURRENCY": (int, 4),
    "METRICS_PORT": (int, 9100),
    "QUEUE_NAME": (str, "grievance:queue"),
    "QUALITY_SAMPLE_RATE": (float, 0.2),
    "OTEL_EXPORTER_OTLP_ENDPOINT": (str, None),
    "SERVICE_NAME": (str, "service"),
    "LOG_LEVEL": (str, "INFO"),
    "SLO_P95_SECONDS": (float, 30.0),
    "ENABLE_FAULT_INJECTION": (int, 0),
    "STALE_RUN_S": (float, 120.0),
    "STUB_DELAY_MS": (int, 800),
    "STUB_FAIL": (int, 0),
}


class _Config:
    def __getattr__(self, name):
        if name not in _SPEC:
            raise AttributeError(name)
        cast, default = _SPEC[name]
        raw = os.environ.get(name)
        if raw is None or raw == "":
            return default
        return cast(raw)

    def require(self, name):
        value = getattr(self, name)
        if value is None:
            raise RuntimeError(f"Required environment variable {name} is not set (see .env.example)")
        return value


cfg = _Config()
