# RepoMedic — Interview Preparation Notes

Everything below is grounded in the actual implementation in this repository.
No scale, users, benchmarks, or production deployment are claimed — this is a
working single-machine tool with a deterministic demo mode and a test suite.

## 30-second explanation

RepoMedic is an automated debugging pipeline. You point it at a repository
with failing tests; it detects the stack, builds an AST-based model of the
code, reproduces the failure in an isolated working copy, generates
root-cause hypotheses from rule-based heuristics, verifies them with real
experiments (like re-running a failing test in isolation), and — only when a
hypothesis has recorded evidence and sufficient confidence — generates a
minimal diff, applies it to a fresh copy, and re-runs first the original
failures and then the whole suite. It refuses to claim success unless those
runs actually pass, and it writes a Markdown/JSON incident report of the
entire investigation.

## 2-minute architecture walkthrough

- `ingest/detector.py` classifies the repo (language, package manager, test
  framework → execution command) from marker files and test-file contents.
- `ingest/graph.py` parses every module with stdlib `ast`, records functions
  (flagging mutable defaults), classes (flagging mutable class attributes),
  and imports; resolves intra-repo imports (including relative ones) into a
  NetworkX digraph; and maps each test module to the source modules it
  transitively reaches.
- `execute/` defines a `Workspace` (disposable copy of the repo) and an
  `Executor` protocol with two implementations: `DockerExecutor` (fresh
  container per command, `--network none`, memory/CPU/pid caps, non-root) and
  `LocalExecutor` (subprocess with scrubbed env and timeout). pytest runs
  with `--junitxml`; `parser.py` turns the XML plus traceback text into
  structured `Failure`/`Frame` models, with a stdout regex fallback.
- `investigate/` holds the explicit state machine (a declared transition
  table; illegal transitions raise), the `AgentProvider` interface, the
  deterministic rule-based investigator, and the experiment runner that maps
  experiment outcomes to confidence updates.
- `patch/` has three AST-guided diff templates, a dependency-free unified
  diff applier (all hunks verified in memory before any write), and a
  two-phase validator.
- `events.py` + `store/db.py`: every action emits a structured event to a
  bus fanned out to the console (the `[STAGE] message` CLI output), SQLite,
  and an in-memory sink used to render the report timeline.
- `engine.py` orchestrates the whole pipeline; `report/` renders the session
  model into Markdown and JSON; `dashboard/app.py` is a read-only FastAPI
  viewer over the SQLite store.

## Exact investigation lifecycle

`CREATED → INGEST → GRAPH → EXECUTE → (NO_FAILURE | OBSERVE) → HYPOTHESIZE →
RANK → (VERIFY → RANK)* → (ROOT_CAUSE | FAILED) → PATCH → VALIDATE →
REGRESSION → REPORT → COMPLETE`, with rejected patches short-circuiting from
VALIDATE/REGRESSION to REPORT. The loop budget is 4 experiments; root-cause
selection requires confidence ≥ 0.6 **and** at least one supporting
`Evidence` record.

## Why an explicit state machine

The failure mode I wanted to make impossible is the pipeline "skipping ahead"
— e.g. patching without a selected root cause. `TRANSITIONS` is data, and a
test asserts PATCH appears as a target only under ROOT_CAUSE. Every
transition also emits an event, so the persisted event stream doubles as an
execution trace.

## Why AgentProvider is an abstraction

The engine needs exactly two capabilities from "intelligence": propose
hypotheses for observed failures, and build a patch for a verified one.
Everything else — isolation, evidence recording, confidence updates,
validation, reporting — is engine policy that shouldn't change when the
intelligence gets smarter. The deterministic provider makes the entire
pipeline testable in CI; an LLM provider would implement the same two
methods and inherit every safety property.

## Evidence and confidence representation

`Evidence` is a Pydantic record (id, kind ∈ {test_failure, traceback,
static_analysis, experiment, validation}, description, data). Hypotheses hold
lists of supporting/contradicting evidence *ids*; `RootCause` carries the ids
it was selected on. Confidence is a float updated only by experiment
verdicts: `supports` → `c += (1−c)·0.8`; `contradicts` → `c ×= 0.25`
(rejected below 0.15); `inconclusive` → unchanged. Both update rules keep c
in [0, 1] (tested).

## How deterministic demo mode works

The default provider is a rule engine over structured inputs: the parsed
failures and the repo model. Rules: mutable default argument, shared mutable
class attribute, schema key mismatch (missing `KeyError` key that
edit-distance-matches a key some producer function returns), plus a fallback
that just localizes the deepest in-repo frame at low prior. Same repo in →
same hypotheses, experiments, patch out. Both bundled fixtures are
investigated end-to-end inside the test suite and in CI.

## How patch validation prevents false success

`validate_patch` copies the repo again, applies the diff with RepoMedic's own
applier (context mismatch → rejected without running anything), then phase 1
re-runs only the originally failing test node ids, phase 2 runs the full
suite. `verdict` becomes `accepted` only on the code path where both runs
executed and were green. Adversarial tests cover: patch doesn't fix, patch
fixes but regresses another test, patch doesn't apply.

## Docker vs local executor tradeoffs

Docker: real isolation boundary (fresh container, no network, resource caps,
non-root) but requires a daemon and pays image/startup cost. Local:
subprocess in the temp workspace with scrubbed environment and timeout —
fast and dependency-free, used for the deterministic test suite, but **no
security sandbox**; the README says so explicitly. Selection is `--executor
auto|docker|local`, auto preferring Docker when `docker info` answers.

## SQLite session model

Two tables: `sessions` (id, created_at, repo_path, state, full session as a
Pydantic JSON blob) and `events` (session_id, seq, timestamp, stage, message,
data JSON; unique on session_id+seq). The whole `InvestigationSession`
round-trips losslessly through `model_dump_json`/`model_validate_json`, which
is what makes `repomedic show`, the dashboard, and post-hoc report rendering
cheap.

## 10 hard questions (with honest answers)

1. **"Your isolation experiment assumes test-order dependence. What if the
   suite is randomized?"** Then the isolation re-run's signal is unreliable;
   I assume plain deterministic pytest ordering and say so in the README.
   Random-order plugins would need N repeated runs and order bisection —
   listed as future work, not implemented.
2. **"The schema heuristic renames the producer key. Why is the producer
   wrong and not the consumer?"** Convention choice: tests encode the
   contract, and the failing expectation lives in tests. If the tests are
   wrong the patch is wrong — but then regression validation catches
   contract-dependent breakage (there's an adversarial test where renaming
   breaks an old-contract test and the patch is rejected), and the report
   shows the reasoning for a human to overrule.
3. **"How do you know the patch is minimal?"** It's template-minimal: each
   template edits only the lines implicated by the hypothesis (measured as
   `lines_changed` from the diff). I don't search a patch space or minimize
   globally — there's no beam of candidates.
4. **"What happens with two plausible root causes?"** Ranking is by
   confidence, stable sort for ties; each hypothesis with an experiment gets
   verified (budget 4). Only the top survivor above 0.6 with evidence is
   selected. Multiple *independent* bugs are a real weakness: one root cause
   is selected per session, and if the patch fixes only its own failures,
   regression validation fails the patch even though it was correct locally.
5. **"Your import resolution — what breaks it?"** Dynamic imports,
   `importlib` calls, namespace packages spread across roots, `sys.path`
   manipulation, and re-exports. It resolves static `import`/`from` against
   the set of repo module names with longest-prefix matching; anything
   dynamic is invisible.
6. **"Why parse junit XML instead of pytest's Python API?"** The executor
   boundary: inside Docker, RepoMedic isn't installed and only artifacts
   cross the boundary. A file-based contract works identically for both
   executors and any future language.
7. **"Is the local executor safe?"** No, and it's documented as not safe:
   temp copy + scrubbed env + timeout is hygiene, not sandboxing. Repository
   code (tests, producer probes) executes with the user's privileges.
8. **"How does confidence 0.8/0.25 justify itself?"** It doesn't come from
   data — it's a fixed likelihood-ratio-shaped update chosen so one
   supporting experiment lifts a plausible prior (~0.4) above the 0.6
   threshold and one contradiction rejects it. The honest framing: ordinal
   ranking with a threshold, not calibrated probability.
9. **"What if pytest hangs?"** Hard timeout per command in both executors;
   the result is marked `timed_out`, experiments map it to `inconclusive`,
   and the run fails honestly. Docker timeout notes the container may need
   cleanup — `docker run` isn't killed with a guaranteed reap.
10. **"Race conditions or concurrency?"** One investigation per process;
    the EventBus takes a lock only for sequence numbering. SQLite access is
    short-lived connections. Concurrent sessions against the same store
    could interleave `next_session_id` (count-based) — a known small race,
    acceptable for a single-user CLI.

## 5 limitations to admit proactively

1. Python + pytest only; other ecosystems are detected and rejected cleanly.
2. Three defect families plus a fallback localizer — everything else ends
   `FAILED` (by design, but it means low recall on arbitrary bugs).
3. The local executor provides no security isolation; Docker is required for
   untrusted code, and the Docker path needs a running daemon.
4. Confidence numbers are heuristic weights, not calibrated probabilities.
5. The diff applier normalizes CRLF files to LF and single-root repos are
   assumed (module names derive from paths relative to one root).

## 3 architectural decisions to defend

1. **Deterministic provider before any LLM.** It forced the interfaces
   (provider/executor/validator) to be real seams and made the full pipeline
   CI-testable; intelligence is swappable without touching safety.
2. **Own unified-diff applier instead of `git apply`/`patch`.** Zero external
   binary dependency (Windows CI runs it), and all-hunks-verify-before-write
   gives atomicity I can test directly (multi-file mismatch test).
3. **Evidence ids as the currency of conclusions.** Root causes and reports
   reference recorded `Evidence` rows rather than prose; the "never claim
   without evidence" rule is checkable (a hostile 0.99-confidence provider
   with no evidence is refused — there's a test).
