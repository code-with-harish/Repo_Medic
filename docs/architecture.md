# RepoMedic Architecture

RepoMedic is organized as a pipeline of narrowly-scoped subsystems glued
together by three cross-cutting contracts: **Pydantic state models**, the
**EventBus**, and the **investigation state machine**.

```
repomedic/
├── cli.py                  CLI entrypoint (click); wires everything together
├── engine.py               InvestigationEngine: drives the FSM end-to-end
├── events.py               Event model + EventBus + sinks (console/SQLite/list)
├── models/                 ALL investigation state, as Pydantic v2 models
│   ├── repo.py             RepoModel, ModuleInfo, FunctionInfo, ClassInfo
│   ├── execution.py        CommandResult, ExecutionResult, Failure, Frame
│   └── investigation.py    Hypothesis, Evidence, Experiment, Session, ...
├── ingest/
│   ├── detector.py         language / package manager / test framework
│   └── graph.py            AST scan + NetworkX import digraph + test map
├── execute/
│   ├── base.py             Executor protocol + Workspace (temp repo copies)
│   ├── local_executor.py   subprocess fallback (env-scrubbed, timeout)
│   ├── docker_executor.py  docker run --rm --network none, mem/cpu caps
│   └── parser.py           junit-xml + traceback parsing into evidence
├── investigate/
│   ├── state_machine.py    explicit transition table, event-emitting
│   ├── provider.py         AgentProvider interface (LLM plug point)
│   ├── heuristics.py       DeterministicInvestigator (rule engine)
│   └── experiments.py      experiment runner + confidence updates
├── patch/
│   ├── generator.py        AST-guided minimal unified-diff templates
│   ├── applier.py          dependency-free unified diff engine
│   └── validator.py        two-phase validation in a fresh workspace
├── report/                 markdown.py + json_report.py renderers
├── store/db.py             SQLite persistence (sessions + event streams)
└── dashboard/app.py        FastAPI read-only viewer over the store
```

## Data flow

1. **INGEST** (`ingest.detector`) — marker files and layout classify the
   repository (language, package manager, test framework) and produce the
   execution command. Unsupported repositories fail fast into `FAILED` with a
   report.
2. **GRAPH** (`ingest.graph`) — every Python module is parsed with the stdlib
   `ast` module. We extract imports (absolute + relative, resolved against the
   set of repo modules with longest-prefix matching), functions/methods
   (flagging mutable defaults), classes (flagging mutable class attributes),
   and build a NetworkX digraph. Test modules are mapped to the source modules
   they transitively reach — this is what later scopes hypothesis search.
3. **EXECUTE** (`execute.*`) — the repository is copied into a disposable
   `Workspace`; pytest runs there with `--junitxml` via the selected
   `Executor`. JUnit XML is the primary result channel (identical for local
   and Docker execution); failure `longrepr` text is parsed into stack frames;
   a stdout regex fallback covers collection crashes.
4. **OBSERVE** — each failure and its deepest in-repo frame are recorded as
   `Evidence` (`test_failure`, `traceback`).
5. **HYPOTHESIZE** (`investigate.heuristics`) — the `AgentProvider` proposes
   hypotheses. Each carries a prior, a suspect `file:line`, static findings
   (recorded as `static_analysis` evidence), an optional experiment, and an
   opaque `patch_context` consumed later by the patch generator.
6. **RANK / VERIFY loop** — hypotheses are ranked by confidence; the best
   unverified experiment runs in the workspace via the Executor. Verdicts
   update confidence multiplicatively (`supports`: c += (1-c)·0.8;
   `contradicts`: c ×= 0.25, rejecting below 0.15) and are recorded as
   `experiment` evidence. The FSM loops VERIFY → RANK until no experiments
   remain or the budget (4) is exhausted.
7. **ROOT_CAUSE** — the top hypothesis is selected only if confidence ≥ 0.6
   **and** it has at least one supporting evidence record. Otherwise the
   session ends `FAILED` (inconclusive) — with a full report of what was
   tried.
8. **PATCH** (`patch.generator`) — deterministic AST-guided templates emit a
   minimal unified diff (e.g. move a shared class attribute into `__init__`;
   replace a mutable default with `None` + guard; rename a producer dict key
   back to the contract the tests encode).
9. **VALIDATE / REGRESSION** (`patch.validator`) — a *fresh* workspace copy is
   patched by RepoMedic's own diff engine, then: phase 1 re-runs only the
   original failing tests; phase 2 runs the full suite. The verdict is
   `accepted` only when both phases actually executed and passed.
10. **REPORT** — Markdown + JSON incident reports are rendered from the
    session model and the captured event timeline, written to
    `<repo>/.repomedic/reports/`, and the session is persisted to SQLite.

## Cross-cutting contracts

### State machine
`investigate/state_machine.py` declares a transition table (`TRANSITIONS`);
`StateMachine.transition` raises on any undeclared edge and emits a `STATE`
event for every move. A test asserts PATCH is reachable *only* from
ROOT_CAUSE — the "no patch without a root cause" rule is structural, not
conventional.

### Events
Every agent action goes through `EventBus.emit(stage, message, **data)`.
Sinks: `ConsoleSink` (the `[STAGE] message` CLI output), `SQLiteSink`
(persistence), `ListSink` (report timeline + tests). Adding observability to
a new step is one `emit` call.

### Evidence discipline
`RootCause` construction requires evidence ids; the engine only selects a
hypothesis with non-empty `supporting_evidence`. `ValidationResult.verdict`
is only set to `accepted` inside the validator after both phases ran green.

## Isolation model

| Layer | Guarantee |
|---|---|
| `Workspace` | source files are never executed or modified in place; RepoMedic writes only `.repomedic/` (reports, session DB) into the target |
| `DockerExecutor` | fresh container per command, `--network none`, memory/CPU/pid caps, non-root user |
| `LocalExecutor` | temp-dir working copy, scrubbed environment, hard timeout — **best effort only** |

The Executor is selected per-run (`--executor auto|docker|local`). `auto`
prefers Docker whenever the daemon answers.

## Extension points

- **AgentProvider** (`investigate/provider.py`): plug in an LLM-backed
  investigator; the engine, executors, validator and reports are unchanged.
- **Heuristic rules**: add `_rule_*` methods returning drafts.
- **Patch templates**: add a generator keyed on `patch_context["kind"]`.
- **Languages**: `detector.py` + a per-language graph builder + result parser.
