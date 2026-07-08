"""Two-phase patch validation in a fresh temporary working copy.

Phase 1: re-run only the originally failing tests — the patch must fix them.
Phase 2: run the full suite — the patch must not regress anything.
The verdict is only `accepted` when both phases actually executed and passed.
"""

from __future__ import annotations

from pathlib import Path

from repomedic.execute.base import DEFAULT_TIMEOUT_S, Executor, Workspace
from repomedic.execute.parser import run_pytest
from repomedic.models.investigation import PatchProposal, ValidationResult
from repomedic.patch.applier import PatchError, apply_unified_diff


def validate_patch(
    repo_root: Path,
    patch: PatchProposal,
    failing_node_ids: list[str],
    test_command: list[str],
    executor: Executor,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> ValidationResult:
    result = ValidationResult()
    with Workspace(repo_root, label="validate") as ws:
        try:
            apply_unified_diff(ws.path, patch.diff)
        except PatchError:
            # Patch does not apply cleanly: reject without claiming any run.
            result.verdict = "rejected_original"
            return result

        original = run_pytest(
            executor, ws.path, base_command=test_command,
            extra_args=failing_node_ids, timeout_s=timeout_s,
        )
        result.original_run = original
        result.original_failures_passed = original.all_green
        if not original.all_green:
            result.verdict = "rejected_original"
            return result

        regression = run_pytest(
            executor, ws.path, base_command=test_command, timeout_s=timeout_s,
        )
        result.regression_run = regression
        result.regression_total = regression.total
        result.regression_passed = regression.all_green
        result.verdict = "accepted" if regression.all_green else "rejected_regression"
        return result
