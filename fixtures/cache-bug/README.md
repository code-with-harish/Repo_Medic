# Fixture: cache-bug

A small in-memory cache library with an intentional **shared mutable state**
bug: `MemoryCache._store` is a *class-level* dict, so every instance — and
therefore every test — shares the same underlying storage.

Expected RepoMedic behavior:
- `tests/test_cache.py::test_new_cache_starts_empty` and
  `tests/test_stats.py::test_stats_fresh_cache` fail in the full run but pass
  in isolation (the classic cross-test state leak signal);
- the `shared_mutable_class_attr` heuristic flags `src/cache.py`;
- the generated patch moves `_store` (and `_hits`/`_misses` stay per-instance
  already) into `__init__`;
- original failures pass and the full suite stays green.
