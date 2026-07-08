from pathlib import Path

from repomedic.ingest.detector import detect
from repomedic.ingest.graph import build_repo_model


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_python_repo(root: Path) -> None:
    write(root, "src/__init__.py", "")
    write(root, "src/cache.py", (
        "class Cache:\n"
        "    store = {}\n"
        "    def get(self, key, default=[]):\n"
        "        return self.store.get(key, default)\n"
    ))
    write(root, "src/api.py", (
        "from src.cache import Cache\n"
        "def handler():\n"
        "    return Cache().get('x')\n"
    ))
    write(root, "tests/test_api.py", (
        "from src.api import handler\n"
        "def test_handler():\n"
        "    assert handler() == []\n"
    ))
    write(root, "pyproject.toml", "[tool.pytest.ini_options]\ntestpaths=['tests']\n")


def test_detect_python_pytest(tmp_path):
    make_python_repo(tmp_path)
    det = detect(tmp_path)
    assert det.language == "python"
    assert det.package_manager == "pip"
    assert det.test_framework == "pytest"
    assert det.test_command[:3] == ["python", "-m", "pytest"]


def test_detect_ignores_venv_and_git(tmp_path):
    write(tmp_path, ".venv/lib/junk.py", "x = 1")
    write(tmp_path, "package.json", "{}")
    det = detect(tmp_path)
    assert det.language == "javascript"


def test_detect_unknown(tmp_path):
    write(tmp_path, "readme.txt", "hello")
    assert detect(tmp_path).language == "unknown"


def test_graph_modules_and_edges(tmp_path):
    make_python_repo(tmp_path)
    model = build_repo_model(tmp_path)
    assert set(model.modules) == {"src", "src.cache", "src.api", "tests.test_api"}
    edge_pairs = {(e.src, e.dst) for e in model.edges}
    assert ("src.api", "src.cache") in edge_pairs
    assert ("tests.test_api", "src.api") in edge_pairs


def test_graph_flags_mutable_defaults_and_class_attrs(tmp_path):
    make_python_repo(tmp_path)
    model = build_repo_model(tmp_path)
    cache = model.modules["src.cache"]
    cls = cache.classes[0]
    assert cls.mutable_class_attrs == {"store": 2}
    get = cls.methods[0]
    assert get.mutable_default_args == ["default"]


def test_test_map_transitive(tmp_path):
    make_python_repo(tmp_path)
    model = build_repo_model(tmp_path)
    assert model.test_map["tests.test_api"] == ["src.api", "src.cache"]


def test_relative_import_resolution(tmp_path):
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/a.py", "from . import b\n")
    write(tmp_path, "pkg/b.py", "VALUE = 1\n")
    write(tmp_path, "tests/test_a.py", "import pkg.a\ndef test_a():\n    assert True\n")
    model = build_repo_model(tmp_path)
    pairs = {(e.src, e.dst) for e in model.edges}
    assert ("pkg.a", "pkg.b") in pairs


def test_parse_error_recorded(tmp_path):
    write(tmp_path, "bad.py", "def broken(:\n")
    write(tmp_path, "tests/test_x.py", "def test_x():\n    assert True\n")
    model = build_repo_model(tmp_path)
    assert model.modules["bad"].parse_error is not None
