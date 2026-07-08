# Fixture: schema-mismatch

A miniature user service where the storage layer was "refactored": the
producer now returns `user_name` / `email_address`, while the API layer and
its tests still consume the original contract key `username`.

Expected RepoMedic behavior:
- `tests/test_api.py` fails with `KeyError: 'username'`;
- the `schema_key_mismatch` heuristic locates the producer dict literal in
  `src/repository.py` whose `user_name` key is a near-match for the missing
  `username`;
- the verification experiment calls the producer in isolation and confirms
  the returned keys;
- the generated patch renames the producer key back to the contract the
  tests encode, and the full suite passes.
