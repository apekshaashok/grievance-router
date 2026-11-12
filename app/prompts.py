"""Prompts are versioned artifacts (L2): files named <role>_<version>.txt. The version and a content hash
are recorded with every response and in every trace."""
import hashlib
import os
from typing import Tuple

from .config import cfg

_cache = {}


def load(role: str, version: str) -> Tuple[str, str]:
    for v in (version, "v1"):
        path = os.path.join(cfg.PROMPTS_DIR, f"{role}_{v}.txt")
        if os.path.exists(path):
            mtime = os.path.getmtime(path)
            if _cache.get(path, (None,))[0] != mtime:
                text = open(path, encoding="utf-8").read()
                _cache[path] = (mtime, text, hashlib.sha256(text.encode()).hexdigest()[:12])
            return _cache[path][1], _cache[path][2]
    raise FileNotFoundError(f"no prompt for role {role!r} (version {version!r}) in {cfg.PROMPTS_DIR}")
