from pathlib import Path

from repomedic.ingest.graph import build_repo_model
from repomedic.investigate.heuristics import DeterministicInvestigator, keys_similar
from repomedic.models.execution import (
    CommandResult,
    ExecutionResult,
    Failure,
    Frame,
    TestCaseResult,
    TestOutcome,
)


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def execution_with(failures: list[Failure]) -> ExecutionResult:
    return ExecutionResult(
        command_result=CommandResult(command=["python", "-m", "pytest"], exit_code=1),
        tests=[TestCaseResult(test_id=f.test_id, outcome=TestOutcome.FAILED)
               for f in failures],
        failures=failures,
        failed=len(failures),
    )


def test_keys_similar():
    assert keys_similar("user_name", "username")
    assert keys_similar("userName", "username")
    assert not keys_similar("username", "username")  # identical is not a mismatch
    assert not keys_similar("token", "username")


def make_cache_repo(root: Path) -> None:
    write(root, "conftest.py", "")
    write(root, "src/__init__.py", "")
    write(root, "src/cache.py", (
        "class Cache:\n"
        "    _store = {}\n"
        "\n"
        "    def put(self, key, value):\n"
        "        self._store[key] = value\n"
        "\n"
        "    def get(self, key):\n"
        "        return self._store.get(key)\n"
    ))
    write(root, "tests/test_cache.py", (
        "from src.cache import Cache\n"
        "def test_put():\n"
        "    c = Cache()\n"
        "    c.put('a', 1)\n"
        "    assert c.get('a') == 1\n"
        "def test_fresh_instance_empty():\n"
        "    assert Cache().get('a') is None\n"
    ))


def test_shared_class_attr_rule(tmp_path):
    make_cache_repo(tmp_path)
    model = build_repo_model(tmp_path)
    failure = Failure(
        test_id="tests.test_cache::test_fresh_instance_empty",
        exception_type="AssertionError",
        message="assert 1 is None",
        frames=[Frame(file="tests/test_cache.py", line=7, function="test_fresh_instance_empty",
                      in_repo=True)],
    )
    investigator = DeterministicInvestigator(repo_root=tmp_path)
    hyps = investigator.generate_hypotheses(model, execution_with([failure]))
    categories = {h.category for h in hyps}
    assert "shared_mutable_class_attr" in categories
    top = next(h for h in hyps if h.category == "shared_mutable_class_attr")
    assert top.suspect.file == "src/cache.py"
    assert top.suspect.line == 2
    assert top.experiment is not None
    assert top.experiment.command is not None
    assert "tests/test_cache.py::test_fresh_instance_empty" in top.experiment.command
    assert top.patch_context["class"] == "Cache"


def test_mutable_default_rule(tmp_path):
    write(tmp_path, "conftest.py", "")
    write(tmp_path, "app.py", (
        "def collect(item, bucket=[]):\n"
        "    bucket.append(item)\n"
        "    return bucket\n"
    ))
    write(tmp_path, "tests/test_app.py", (
        "from app import collect\n"
        "def test_collect():\n"
        "    assert collect(1) == [1]\n"
        "def test_collect_fresh():\n"
        "    assert collect(2) == [2]\n"
    ))
    model = build_repo_model(tmp_path)
    failure = Failure(
        test_id="tests.test_app::test_collect_fresh",
        exception_type="AssertionError",
        message="assert [1, 2] == [2]",
        frames=[Frame(file="app.py", line=3, function="collect", in_repo=True)],
    )
    hyps = DeterministicInvestigator(tmp_path).generate_hypotheses(
        model, execution_with([failure]))
    top = next(h for h in hyps if h.category == "mutable_default_argument")
    # frame in same file => boosted prior
    assert top.prior > 0.4
    assert top.patch_context["qualname"] == "collect"
    assert top.patch_context["args"] == ["bucket"]


def test_schema_mismatch_rule(tmp_path):
    write(tmp_path, "conftest.py", "")
    write(tmp_path, "svc/__init__.py", "")
    write(tmp_path, "svc/users.py", (
        "def get_user():\n"
        "    return {'user_name': 'ada', 'id': 1}\n"
    ))
    write(tmp_path, "svc/views.py", (
        "from svc.users import get_user\n"
        "def render_user():\n"
        "    return get_user()['username']\n"
    ))
    write(tmp_path, "tests/test_views.py", (
        "from svc.views import render_user\n"
        "def test_render():\n"
        "    assert render_user() == 'ada'\n"
    ))
    model = build_repo_model(tmp_path)
    failure = Failure(
        test_id="tests.test_views::test_render",
        exception_type="KeyError",
        message="'username'",
        frames=[
            Frame(file="tests/test_views.py", line=3, function="test_render", in_repo=True),
            Frame(file="svc/views.py", line=3, function="render_user", in_repo=True),
        ],
    )
    hyps = DeterministicInvestigator(tmp_path).generate_hypotheses(
        model, execution_with([failure]))
    top = next(h for h in hyps if h.category == "schema_key_mismatch")
    assert top.suspect.file == "svc/users.py"
    assert top.patch_context["old_key"] == "user_name"
    assert top.patch_context["new_key"] == "username"
    assert top.experiment.script is not None
    assert "VERDICT" in top.experiment.script


def test_fallback_rule_always_produces_hypothesis(tmp_path):
    write(tmp_path, "conftest.py", "")
    write(tmp_path, "calc.py", "def add(a, b):\n    return a - b\n")
    write(tmp_path, "tests/test_calc.py", (
        "from calc import add\n"
        "def test_add():\n"
        "    assert add(1, 1) == 2\n"
    ))
    model = build_repo_model(tmp_path)
    failure = Failure(
        test_id="tests.test_calc::test_add",
        exception_type="AssertionError",
        message="assert 0 == 2",
        frames=[Frame(file="calc.py", line=2, function="add", in_repo=True)],
    )
    hyps = DeterministicInvestigator(tmp_path).generate_hypotheses(
        model, execution_with([failure]))
    assert len(hyps) >= 1
    assert hyps[0].hypothesis_id == "H1"
    assert all(h.patch_context.get("static_findings") for h in hyps)
