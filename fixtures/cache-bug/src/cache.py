"""A tiny in-memory cache with hit/miss accounting.

BUG (intentional, for RepoMedic): `_store` is declared as a class attribute,
so all MemoryCache instances share one dict. State written through any
instance is visible to every other instance.
"""


class CacheMiss(KeyError):
    """Raised by `fetch` when a key is absent."""


class MemoryCache:
    # Shared across ALL instances — this is the defect under investigation.
    _store = {}

    def put(self, key, value):
        self._store[key] = value
        return value

    def get(self, key, default=None):
        return self._store.get(key, default)

    def fetch(self, key):
        if key not in self._store:
            raise CacheMiss(key)
        return self._store[key]

    def delete(self, key):
        self._store.pop(key, None)

    def size(self):
        return len(self._store)

    def keys(self):
        return sorted(self._store)
