import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("REDIS_URL", "redis://unused:6379/0")
os.environ["LLM_PROVIDER"] = "rules"
os.environ["EMBEDDER"] = "hash"          # offline, deterministic; the containers use sentence-transformers
os.environ["ENABLE_FAULT_INJECTION"] = "1"
os.environ["QUALITY_SAMPLE_RATE"] = "1"


@pytest.fixture(scope="session", autouse=True)
def built_index():
    from app import ingest
    from app.retrieval import index as index_mod
    d = tempfile.mkdtemp()
    os.environ["INDEX_DIR"] = d
    ingest.build(index_dir=d)
    index_mod.clear_cache()
    return d


@pytest.fixture()
def store():
    import fakeredis
    from app.store import Store
    return Store(fakeredis.FakeRedis(decode_responses=True))


GOOD = {"investor_ref": "INV123456", "complaint_text": "I invested in a fixed deposit through my bank's app. The interest credited is lower than what I was promised. I want to complain to SEBI.",
        "channel": "web", "complaint_date": "2026-10-20"}
