from repomedic.execute.local_executor import LocalExecutor
from repomedic.investigate.experiments import run_experiment, update_confidence
from repomedic.models.investigation import (
    Experiment,
    ExperimentStatus,
    Hypothesis,
    HypothesisStatus,
)


def make_hypothesis(confidence: float = 0.41) -> Hypothesis:
    return Hypothesis(
        hypothesis_id="H1", description="d", category="c",
        prior=confidence, confidence=confidence,
    )


def test_script_experiment_supports(tmp_path):
    exp = Experiment(experiment_id="X1", description="d",
                     script="print('VERDICT:SUPPORTS')\n")
    verdict = run_experiment(LocalExecutor(), tmp_path, exp)
    assert verdict == "supports"
    assert exp.status == ExperimentStatus.RAN
    assert exp.command_result.exit_code == 0
    assert not (tmp_path / ".repomedic-experiment.py").exists()  # cleaned up


def test_script_experiment_contradicts(tmp_path):
    exp = Experiment(experiment_id="X1", description="d",
                     script="print('VERDICT:CONTRADICTS')\n")
    assert run_experiment(LocalExecutor(), tmp_path, exp) == "contradicts"


def test_script_experiment_crash_is_inconclusive(tmp_path):
    exp = Experiment(experiment_id="X1", description="d", script="raise RuntimeError\n")
    assert run_experiment(LocalExecutor(), tmp_path, exp) == "inconclusive"


def test_command_experiment_exit_codes(tmp_path):
    ok = Experiment(experiment_id="X1", description="d",
                    command=["python", "-c", "raise SystemExit(0)"])
    assert run_experiment(LocalExecutor(), tmp_path, ok) == "supports"
    bad = Experiment(experiment_id="X2", description="d",
                     command=["python", "-c", "raise SystemExit(1)"])
    assert run_experiment(LocalExecutor(), tmp_path, bad) == "contradicts"


def test_confidence_update_supports():
    hyp = make_hypothesis(0.41)
    before, after = update_confidence(hyp, "supports")
    assert before == 0.41
    assert after > 0.85
    assert hyp.status == HypothesisStatus.VERIFIED


def test_confidence_update_contradicts_rejects():
    hyp = make_hypothesis(0.41)
    _, after = update_confidence(hyp, "contradicts")
    assert after < 0.15
    assert hyp.status == HypothesisStatus.REJECTED


def test_confidence_update_inconclusive_is_neutral():
    hyp = make_hypothesis(0.41)
    before, after = update_confidence(hyp, "inconclusive")
    assert before == after == 0.41
    assert hyp.status == HypothesisStatus.OPEN
