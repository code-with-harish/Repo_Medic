# Contributing to RepoMedic

Thanks for your interest! RepoMedic is early and contributions are welcome.

## Development setup

```bash
git clone <your-fork>
cd repomedic
python -m venv .venv && . .venv/bin/activate   # .venv\Scripts\activate on Windows
make install          # pip install -e ".[dev]"
make test             # unit + e2e tests, no Docker required
make test-all         # includes Docker-executor tests (needs a running daemon)
make demo             # investigate both bundled fixture repos
```

## Ground rules

1. **Evidence discipline is non-negotiable.** Never add a code path that
   reports a root cause without recorded `Evidence`, or marks a patch
   `accepted` without validation commands actually running and passing.
   `tests/test_state_machine.py::test_cannot_patch_without_root_cause` and the
   validator tests enforce this — keep them green.
2. **Determinism first.** The `DeterministicInvestigator` must stay fully
   deterministic: same repository in, same hypotheses out. Anything
   heuristic-with-randomness belongs in a new `AgentProvider`.
3. **The user's source files are read-only.** All execution, experiments and
   patching happen in `Workspace` copies; the only writes into the target
   repository are session artifacts under `.repomedic/`. The e2e tests hash
   every source file before and after an investigation and assert equality.
4. **Every agent action emits an event.** New pipeline steps must emit through
   the `EventBus` so the CLI, SQLite store and reports all see them.

## Adding a new heuristic rule

1. Add a `_rule_*` method to `DeterministicInvestigator` returning `_Draft`s
   with a prior, static findings, an experiment (if verifiable) and a
   `patch_context`.
2. If the rule can be auto-patched, add a template to
   `repomedic/patch/generator.py` keyed on `patch_context["kind"]`.
3. Add a unit test in `tests/test_heuristics.py` and, ideally, a fixture
   repository under `fixtures/` plus an e2e test.

## Adding an LLM provider

Implement `repomedic.investigate.provider.AgentProvider` (two methods:
`generate_hypotheses`, `propose_patch`). Keep all repository execution behind
the `Executor` interface so isolation guarantees hold.

## Pull requests

- `make lint && make test` must pass.
- New behavior needs tests; bug fixes need a regression test.
- Keep patches minimal and focused — the same standard RepoMedic holds itself to.
