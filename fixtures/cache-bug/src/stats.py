"""Cache statistics helper built on top of MemoryCache."""

from src.cache import MemoryCache


def summarize(cache: MemoryCache) -> dict:
    return {
        "entries": cache.size(),
        "keys": cache.keys(),
    }


def warm(cache: MemoryCache, pairs) -> int:
    count = 0
    for key, value in pairs:
        cache.put(key, value)
        count += 1
    return count
