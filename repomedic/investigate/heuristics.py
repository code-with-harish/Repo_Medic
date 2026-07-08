"""Deterministic heuristic investigator.

Rule-based `AgentProvider` used in demo mode and CI. Each rule inspects the
structured failures plus the repository model and yields hypotheses with:
prior confidence, suspect location, a verification experiment when one can be
run safely, and a `patch_context` payload consumed by the patch generator.

Rules implemented:
- mutable_default_argument: a touched function declares a list/dict/set
  default (state leaks across calls).
- shared_mutable_class_attr: a touched class declares a mutable class-level
  attribute (state leaks across instances).
- schema_key_mismatch: a KeyError whose missing key closely matches a key a
  producer function actually returns (interface drift after a rename).
- unlocalized_failure: fallback anchored at the deepest in-repo frame.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from repomedic.models.execution import ExecutionResult, Failure
from repomedic.models.investigation import Experiment, Hypothesis, SuspectLocation
from repomedic.models.repo import ModuleInfo, RepoModel

MAX_KEY_EDIT_DISTANCE = 3


def _edit_distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > MAX_KEY_EDIT_DISTANCE:
        return MAX_KEY_EDIT_DISTANCE + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def keys_similar(a: str, b: str) -> bool:
    if a == b:
        return False  # identical keys are not a mismatch
    norm_a = a.replace("_", "").replace("-", "").casefold()
    norm_b = b.replace("_", "").replace("-", "").casefold()
    if norm_a == norm_b:
        return True
    return _edit_distance(a.casefold(), b.casefold()) <= MAX_KEY_EDIT_DISTANCE


def _test_module_name(test_id: str, repo: RepoModel) -> str | None:
    """Map 'tests.test_api::test_x' or 'tests/test_api.py::test_x' to a module."""
    head = test_id.split("::", 1)[0]
    if head.endswith(".py"):
        head = head[:-3].replace("/", ".").replace("\\", ".")
    probe = head
    while probe:
        if probe in repo.modules:
            return probe
        probe = probe.rpartition(".")[0]
    return None


def _touched_modules(failure: Failure, repo: RepoModel) -> list[ModuleInfo]:
    """Source modules implicated by a failure: traceback frames first, then the
    failing test's transitive imports."""
    ordered: list[str] = []
    for frame in reversed(failure.frames):
        if frame.in_repo:
            info = repo.module_for_path(frame.file)
            if info and not info.is_test and info.module not in ordered:
                ordered.append(info.module)
    test_module = _test_module_name(failure.test_id, repo)
    if test_module:
        for name in repo.test_map.get(test_module, []):
            if name not in ordered:
                ordered.append(name)
    return [repo.modules[name] for name in ordered]


def _pytest_node_id(failure: Failure, repo: RepoModel) -> str | None:
    """Rebuild a runnable pytest node id from a junit-style test id."""
    head, _, tail = failure.test_id.partition("::")
    if head.endswith(".py"):
        return failure.test_id
    module = _test_module_name(failure.test_id, repo)
    if module is None:
        return None
    path = repo.modules[module].path
    # Anything between the module and the test name is a test class chain.
    suffix = head[len(module):].strip(".")
    parts = [path] + ([*suffix.split(".")] if suffix else []) + [tail]
    return "::".join(p for p in parts if p)


@dataclass
class _Draft:
    description: str
    category: str
    prior: float
    suspect: SuspectLocation | None
    static_evidence: list[str]
    experiment: Experiment | None
    patch_context: dict


class DeterministicInvestigator:
    """Deterministic rule-based AgentProvider (no LLM required)."""

    name = "deterministic-heuristics"

    def __init__(self, repo_root: str | Path | None = None) -> None:
        self._root_override = Path(repo_root) if repo_root else None

    # ------------------------------------------------------------------ #
    # hypothesis generation
    # ------------------------------------------------------------------ #

    def generate_hypotheses(
        self, repo: RepoModel, execution: ExecutionResult
    ) -> list[Hypothesis]:
        drafts: list[_Draft] = []
        seen: set[tuple[str, str, int]] = set()  # (category, file, line) dedupe

        for failure in execution.failures:
            produced = (
                self._rule_schema_key_mismatch(failure, repo)
                + self._rule_mutable_default(failure, repo)
                + self._rule_shared_class_attr(failure, repo)
            )
            if not produced:
                produced = self._rule_fallback(failure, repo)
            for draft in produced:
                key = (
                    draft.category,
                    draft.suspect.file if draft.suspect else "",
                    draft.suspect.line if draft.suspect else 0,
                )
                if key not in seen:
                    seen.add(key)
                    drafts.append(draft)

        drafts.sort(key=lambda d: d.prior, reverse=True)
        hypotheses: list[Hypothesis] = []
        for idx, draft in enumerate(drafts, 1):
            hyp = Hypothesis(
                hypothesis_id=f"H{idx}",
                description=draft.description,
                category=draft.category,
                prior=round(draft.prior, 2),
                confidence=round(draft.prior, 2),
                suspect=draft.suspect,
                experiment=draft.experiment,
                patch_context=draft.patch_context,
            )
            # Static findings are packed into patch_context for the engine to
            # record as Evidence (evidence ids are session-scoped).
            hyp.patch_context["static_findings"] = draft.static_evidence
            if draft.experiment:
                hyp.experiment.experiment_id = f"X{idx}"
            hypotheses.append(hyp)
        return hypotheses

    # ------------------------------------------------------------------ #
    # rules
    # ------------------------------------------------------------------ #

    def _rule_mutable_default(self, failure: Failure, repo: RepoModel) -> list[_Draft]:
        drafts: list[_Draft] = []
        frame = failure.deepest_repo_frame()
        for info in _touched_modules(failure, repo):
            for func in info.all_functions():
                if not func.mutable_default_args:
                    continue
                prior = 0.40
                findings = [
                    f"static analysis: {info.path}:{func.lineno} `{func.qualname}` "
                    f"declares mutable default(s) {func.mutable_default_args}"
                ]
                if frame and frame.file == info.path:
                    prior += 0.15
                    findings.append(
                        f"traceback: deepest in-repo frame is in the same file "
                        f"({frame.file}:{frame.line})"
                    )
                drafts.append(_Draft(
                    description=(
                        f"`{func.qualname}` in {info.path} uses mutable default "
                        f"argument(s) {func.mutable_default_args}; the default object "
                        f"is shared across calls, leaking state between tests"
                    ),
                    category="mutable_default_argument",
                    prior=prior,
                    suspect=SuspectLocation(file=info.path, line=func.lineno,
                                            symbol=func.qualname),
                    static_evidence=findings,
                    experiment=self._isolation_experiment(failure, repo),
                    patch_context={
                        "kind": "mutable_default_argument",
                        "file": info.path,
                        "qualname": func.qualname,
                        "args": func.mutable_default_args,
                    },
                ))
        return drafts

    def _rule_shared_class_attr(self, failure: Failure, repo: RepoModel) -> list[_Draft]:
        drafts: list[_Draft] = []
        frame = failure.deepest_repo_frame()
        for info in _touched_modules(failure, repo):
            for cls in info.classes:
                for attr, lineno in cls.mutable_class_attrs.items():
                    prior = 0.40
                    findings = [
                        f"static analysis: {info.path}:{lineno} class `{cls.name}` "
                        f"declares mutable class-level attribute `{attr}` shared by "
                        f"all instances"
                    ]
                    if frame and frame.file == info.path:
                        prior += 0.15
                        findings.append(
                            f"traceback: deepest in-repo frame is in the same file "
                            f"({frame.file}:{frame.line})"
                        )
                    drafts.append(_Draft(
                        description=(
                            f"class `{cls.name}` in {info.path} shares mutable "
                            f"class attribute `{attr}` across all instances; state "
                            f"written by one test leaks into the next"
                        ),
                        category="shared_mutable_class_attr",
                        prior=prior,
                        suspect=SuspectLocation(file=info.path, line=lineno,
                                                symbol=f"{cls.name}.{attr}"),
                        static_evidence=findings,
                        experiment=self._isolation_experiment(failure, repo),
                        patch_context={
                            "kind": "shared_mutable_class_attr",
                            "file": info.path,
                            "class": cls.name,
                            "attr": attr,
                        },
                    ))
        return drafts

    def _rule_schema_key_mismatch(self, failure: Failure, repo: RepoModel) -> list[_Draft]:
        if failure.exception_type != "KeyError":
            return []
        missing = failure.message.strip().strip("'\"")
        if not missing:
            return []
        drafts: list[_Draft] = []
        root = self._root_override or Path(repo.root)
        for info in _touched_modules(failure, repo):
            for producer in self._find_similar_key_producers(root, info, missing):
                func, old_key, key_line = producer
                findings = [
                    f"static analysis: {info.path}:{key_line} `{func}` returns a dict "
                    f"with key '{old_key}' but the failing code expects '{missing}' "
                    f"(likely renamed interface)"
                ]
                drafts.append(_Draft(
                    description=(
                        f"schema mismatch: consumer expects key '{missing}' but "
                        f"producer `{func}` in {info.path} returns '{old_key}'"
                    ),
                    category="schema_key_mismatch",
                    prior=0.45,
                    suspect=SuspectLocation(file=info.path, line=key_line, symbol=func),
                    static_evidence=findings,
                    experiment=self._producer_keys_experiment(
                        info.module, func, missing, old_key
                    ),
                    patch_context={
                        "kind": "schema_key_mismatch",
                        "file": info.path,
                        "function": func,
                        "old_key": old_key,
                        "new_key": missing,
                        "line": key_line,
                    },
                ))
        return drafts

    def _rule_fallback(self, failure: Failure, repo: RepoModel) -> list[_Draft]:
        frame = failure.deepest_repo_frame()
        if frame is None:
            return []
        return [_Draft(
            description=(
                f"{failure.exception_type or 'failure'} raised at "
                f"{frame.file}:{frame.line}; no specific defect pattern matched"
            ),
            category="unlocalized_failure",
            prior=0.20,
            suspect=SuspectLocation(file=frame.file, line=frame.line,
                                    symbol=frame.function),
            static_evidence=[
                f"traceback: deepest in-repo frame {frame.file}:{frame.line} "
                f"in `{frame.function}`"
            ],
            experiment=None,
            patch_context={"kind": "none"},
        )]

    # ------------------------------------------------------------------ #
    # experiments
    # ------------------------------------------------------------------ #

    def _isolation_experiment(self, failure: Failure, repo: RepoModel) -> Experiment | None:
        node_id = _pytest_node_id(failure, repo)
        if node_id is None:
            return None
        return Experiment(
            experiment_id="X?",
            description=(
                f"run {node_id} alone: if it passes in isolation but failed in the "
                f"full suite, cross-test shared state is confirmed"
            ),
            command=["python", "-m", "pytest", "-q", node_id],
            supports_on_exit_zero=True,
        )

    def _producer_keys_experiment(
        self, module: str, func: str, missing: str, old_key: str
    ) -> Experiment:
        script = f"""\
import importlib, inspect

mod = importlib.import_module({module!r})
fn = getattr(mod, {func!r}, None)
if fn is None or not callable(fn):
    print("VERDICT:INCONCLUSIVE"); raise SystemExit(0)
sig = inspect.signature(fn)
required = [p for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
if required:
    print("VERDICT:INCONCLUSIVE"); raise SystemExit(0)
out = fn()
if not isinstance(out, dict):
    print("VERDICT:INCONCLUSIVE")
elif {missing!r} not in out and {old_key!r} in out:
    print("VERDICT:SUPPORTS")
elif {missing!r} in out:
    print("VERDICT:CONTRADICTS")
else:
    print("VERDICT:INCONCLUSIVE")
"""
        return Experiment(
            experiment_id="X?",
            description=(
                f"call producer `{module}.{func}` in isolation and inspect returned "
                f"keys: presence of '{old_key}' without '{missing}' confirms the "
                f"schema mismatch"
            ),
            script=script,
        )

    # ------------------------------------------------------------------ #
    # static helpers
    # ------------------------------------------------------------------ #

    def _find_similar_key_producers(
        self, root: Path, info: ModuleInfo, missing: str
    ) -> list[tuple[str, str, int]]:
        """Find functions in `info` containing dict literals with a key similar
        to the missing one. Returns (qualname, old_key, lineno)."""
        path = root / info.path
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            return []
        out: list[tuple[str, str, int]] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    for key in sub.keys:
                        if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                                and keys_similar(key.value, missing)):
                            out.append((node.name, key.value, key.lineno))
        return out

    # ------------------------------------------------------------------ #
    # patching (delegates to the template-based generator)
    # ------------------------------------------------------------------ #

    def propose_patch(self, repo: RepoModel, hypothesis: Hypothesis):
        from repomedic.patch.generator import generate_patch

        root = self._root_override or Path(repo.root)
        return generate_patch(root, hypothesis)
