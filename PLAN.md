# RepoMedic — Implementation Plan

Autonomous repository failure investigation and patch verification system.

## Environment constraints discovered

- Host has Python 3.13 / 3.11 (no 3.12). Target `requires-python >= 3.11`; CI matrix pins 3.12.
- Docker CLI present; daemon may be offline. Execution layer therefore has two
  `Executor` implementations behind one interface: `DockerExecutor` (preferred,
  network-disabled, resource-limited) and `LocalExecutor` (subprocess in a
  temp working copy) selected automatically, overridable via CLI flag.

## Architecture

```
repomedic/
  cli.py                    CLI entrypoint (click)
  events.py                 structured event bus (Pydantic events, sinks)
  models/                   all investigation state as Pydantic models
    repo.py                 RepoModel, ModuleInfo, FunctionInfo, ImportEdge
    execution.py            ExecutionResult, TestCaseResult, Failure, Frame
    investigation.py        Hypothesis, Evidence, Experiment, InvestigationSession
  ingest/
    detector.py             language / package manager / test framework detection
    graph.py                AST import graph (NetworkX), test<->source mapping
  execute/
    base.py                 Executor protocol + workspace management
    local_executor.py       subprocess executor (temp copy, timeout, env scrub)
    docker_executor.py      docker run --rm --network none, mem/cpu limits
    parser.py               pytest junit-xml + stdout failure parser
  investigate/
    state_machine.py        explicit FSM with transition table + event emission
    provider.py             AgentProvider interface (LLM pluggable later)
    heuristics.py           DeterministicInvestigator (rule-based hypothesis gens)
    experiments.py          safe verification experiments (run via Executor)
  patch/
    generator.py            minimal unified diff generation (AST-guided)
    applier.py              apply patch in temp working copy
    validator.py            re-run original failures + full regression suite
  report/
    markdown.py, json_report.py
  store/db.py               SQLite session + event persistence
  dashboard/app.py          FastAPI: sessions, events, reports + minimal HTML
fixtures/
  cache-bug/                mutable shared-state cache bug (class-attr dict)
  schema-mismatch/          producer renamed dict key, consumer KeyError
tests/                      unit + end-to-end integration on both fixtures
```

## Investigation lifecycle (FSM)

INGEST -> GRAPH -> EXECUTE -> OBSERVE -> HYPOTHESIZE -> RANK -> VERIFY ->
(loop VERIFY/UPDATE) -> ROOT_CAUSE -> PATCH -> VALIDATE -> REGRESSION ->
REPORT | FAILED

Rules enforced by the engine, not convention:
- A root cause cannot be selected without >=1 supporting Evidence record.
- A patch is only marked successful when validation commands actually ran and passed.

## Milestones

- [x] M0: plan, environment probe
- [x] M1: package scaffold, pyproject, models, event bus, state machine, SQLite store + unit tests
- [x] M2: ingest — detector + AST import graph + test-source mapping + tests
- [x] M3: execution — LocalExecutor, DockerExecutor, pytest parser + tests
- [x] M4: investigation — provider interface, deterministic heuristics, experiments, confidence updates + tests
- [x] M5: patch — generator, applier, validator + tests
- [x] M6: fixtures (cache-bug, schema-mismatch), CLI, reports, end-to-end integration test
- [x] M7: dashboard, Dockerfile, Makefile, CI, README, CONTRIBUTING, LICENSE, docs/architecture.md

## Progress log

- M0 done: environment probed (Py 3.11 venv — the host's 3.13 launcher entry
  is stale; Docker daemon offline, so executors are auto-selected), plan written.
- M1 done: Pydantic models, event bus, FSM with declared transition table,
  SQLite store. 10 tests.
- M2 done: detector + AST import graph (NetworkX) + transitive test map.
  Fixed relative-import candidate construction (`from . import x` produced
  `..x`). 18 tests.
- M3 done: Workspace + LocalExecutor + DockerExecutor + junit-xml/stdout
  pytest parser. Fixed longrepr frame regex (pytest emits `file:line: ExcType`
  for the final frame). 25 tests.
- M4 done: AgentProvider protocol; DeterministicInvestigator with rules
  mutable_default_argument / shared_mutable_class_attr / schema_key_mismatch /
  unlocalized fallback; experiment runner (isolation re-run + producer probe)
  with multiplicative confidence updates. 46 tests.
- M5 done: AST-guided patch templates, dependency-free unified-diff applier
  (all-hunks-verify-before-write), two-phase validator. 46 tests.
- M6 done: both fixture repos investigate end-to-end via engine and CLI with
  the local executor: cache-bug → shared_mutable_class_attr at src/cache.py:15,
  0.40→0.88, 3-line patch, 7/7 regression PASS; schema-mismatch →
  schema_key_mismatch at src/repository.py:18, 0.45→0.89, 2-line patch, 6/6
  regression PASS. Fixed report final-state rendering + multi-line junit
  messages. 56 tests.
- M7 done: FastAPI dashboard + tests, Dockerfile, Makefile, GitHub Actions CI
  (test matrix + docker-executor job + live fixture-demo job), README with
  Mermaid architecture/lifecycle diagrams and unedited demo transcripts,
  CONTRIBUTING, MIT LICENSE, docs/architecture.md. Ruff clean. Final suite:
  62 passed, 1 skipped (docker-marked test; daemon unavailable on this host —
  Docker Desktop would not start unattended; the docker executor is exercised
  in CI's ubuntu job).
- Final verification (post-commit): full suite 62 passed / 1 skipped, ruff
  clean, both fixture demos re-run from a clean state with identical output
  (exit 0), CLI --version/--help/sessions/show verified, working tree
  identical to HEAD with only gitignored artifacts untracked.

## Adversarial audit (post-v1)

- Claims tightened for precision: "never mutated in place" → "source files
  never executed/modified; RepoMedic writes only `.repomedic/` into the
  target" (README, architecture.md, CONTRIBUTING, engine docstring); diff
  applier's LF-normalization and trailing-newline behavior documented.
- Honesty bug fixed: a test command exiting non-zero with no parseable
  results previously emitted "[FAILURE] 0 failing tests detected"; it now
  reports the exit code honestly, and an empty hypothesis set is reported as
  "No hypotheses generated" rather than "All hypotheses rejected".
- e2e invariant strengthened: every investigation now snapshots and compares
  every file of the target repo (byte-exact, excluding `.repomedic/`).
- New adversarial suite (tests/test_adversarial.py, 14 tests): syntax error
  breaking collection, innocent mutable default (experiment contradicts →
  rejected, no false positive), patch that fixes the original failure but
  regresses another test (rejected_regression, no PASS event), no-pattern
  failure (fallback stays below threshold → FAILED), hostile provider with
  0.99 confidence and zero evidence (refused), inconclusive experiment
  (confidence unchanged, hypothesis stays open), unparseable run, confidence
  bounds under repeated updates, and applier abuse: multi-hunk, multi-file
  atomicity, repeated identical lines, missing trailing newline, CRLF input.
- docs/INTERVIEW.md added: honest Q&A, limitations, and defended decisions.
- Audit verification: 75 passed / 1 skipped (docker, daemon-gated), ruff
  clean, both fixture demos green from a clean state (exit 0).
