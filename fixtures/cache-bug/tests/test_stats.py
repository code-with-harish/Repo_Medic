from src.cache import MemoryCache
from src.stats import summarize, warm


def test_warm_counts():
    cache = MemoryCache()
    assert warm(cache, [("x", 1), ("y", 2)]) == 2


def test_stats_fresh_cache():
    cache = MemoryCache()
    assert summarize(cache) == {"entries": 0, "keys": []}
