import pytest

from src.cache import CacheMiss, MemoryCache


def test_put_and_get():
    cache = MemoryCache()
    cache.put("alpha", 1)
    assert cache.get("alpha") == 1


def test_get_default():
    cache = MemoryCache()
    assert cache.get("nope", default="fallback") == "fallback"


def test_fetch_missing_raises():
    cache = MemoryCache()
    with pytest.raises(CacheMiss):
        cache.fetch("missing-key")


def test_delete_is_idempotent():
    cache = MemoryCache()
    cache.put("beta", 2)
    cache.delete("beta")
    cache.delete("beta")
    assert cache.get("beta") is None


def test_new_cache_starts_empty():
    cache = MemoryCache()
    assert cache.size() == 0
    assert cache.keys() == []
