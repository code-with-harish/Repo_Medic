"""Run verification experiments and update hypothesis confidence.

Experiments execute inside the isolated workspace via the configured
Executor — never against the user's original repository. Confidence moves
only in response to a recorded experiment outcome.
"""

from __future__ import annotations

from pathlib import Path

from repomedic.execute.base import DEFAULT_TIMEOUT_S, Executor
from repomedic.models.investigation import (
    Experiment,
    ExperimentStatus,
    Hypothesis,
    HypothesisStatus,
)

EXPERIMENT_FILENAME = ".repomedic-experiment.py"

# Confidence update factors (simple likelihood-ratio style updates).
SUPPORT_GAIN = 0.8      # c' = c + (1 - c) * SUPPORT_GAIN
CONTRADICT_FACTOR = 0.25  # c' = c * CONTRADICT_FACTOR
REJECT_THRESHOLD = 0.15


def run_experiment(
    executor: Executor,
    workdir: Path,
    experiment: Experiment,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> str:
    """Execute one experiment; returns verdict: supports/contradicts/inconclusive."""
    if experiment.command:
        result = executor.run(workdir, experiment.command, timeout_s=timeout_s)
        experiment.command_result = result
        if result.timed_out:
            verdict = "inconclusive"
        elif result.exit_code == 0:
            verdict = "supports" if experiment.supports_on_exit_zero else "contradicts"
        else:
            verdict = "contradicts" if experiment.supports_on_exit_zero else "supports"
    elif experiment.script:
        script_path = Path(workdir) / EXPERIMENT_FILENAME
        script_path.write_text(experiment.script, encoding="utf-8")
        try:
            result = executor.run(
                workdir, ["python", EXPERIMENT_FILENAME], timeout_s=timeout_s
            )
        finally:
            script_path.unlink(missing_ok=True)
        experiment.command_result = result
        stdout = result.stdout
        if "VERDICT:SUPPORTS" in stdout:
            verdict = "supports"
        elif "VERDICT:CONTRADICTS" in stdout:
            verdict = "contradicts"
        else:
            verdict = "inconclusive"
    else:
        verdict = "inconclusive"

    experiment.status = ExperimentStatus.RAN
    experiment.verdict = verdict
    return verdict


def update_confidence(hypothesis: Hypothesis, verdict: str) -> tuple[float, float]:
    """Apply the confidence update for an experiment verdict.

    Returns (before, after)."""
    before = hypothesis.confidence
    if verdict == "supports":
        after = before + (1.0 - before) * SUPPORT_GAIN
        hypothesis.status = HypothesisStatus.VERIFIED
    elif verdict == "contradicts":
        after = before * CONTRADICT_FACTOR
        if after < REJECT_THRESHOLD:
            hypothesis.status = HypothesisStatus.REJECTED
    else:
        after = before
    hypothesis.confidence = round(after, 2)
    return round(before, 2), hypothesis.confidence
