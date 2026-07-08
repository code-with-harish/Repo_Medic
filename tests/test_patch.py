from pathlib import Path

import pytest

from repomedic.models.investigation import Hypothesis
from repomedic.patch.applier import PatchError, apply_unified_diff, parse_unified_diff
from repomedic.patch.generator import generate_patch


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def hyp(ctx: dict) -> Hypothesis:
    return Hypothesis(hypothesis_id="H1", description="d", category=ctx["kind"],
                      patch_context=ctx)


# --------------------------------------------------------------------- #
# generator
# --------------------------------------------------------------------- #

def test_patch_mutable_default(tmp_path):
    write(tmp_path, "app.py", (
        "def collect(item, bucket=[]):\n"
        "    bucket.append(item)\n"
        "    return bucket\n"
    ))
    patch = generate_patch(tmp_path, hyp({
        "kind": "mutable_default_argument", "file": "app.py",
        "qualname": "collect", "args": ["bucket"],
    }))
    assert patch is not None
    apply_unified_diff(tmp_path, patch.diff)
    text = (tmp_path / "app.py").read_text(encoding="utf-8")
    assert "bucket=None" in text
    assert "if bucket is None:" in text
    # Behavior check: repeated calls no longer share state.
    scope = {}
    exec(text, scope)
    assert scope["collect"](1) == [1]
    assert scope["collect"](2) == [2]


def test_patch_shared_class_attr_with_init(tmp_path):
    write(tmp_path, "cache.py", (
        "class Cache:\n"
        "    _store = {}\n"
        "\n"
        "    def __init__(self, limit=10):\n"
        "        self.limit = limit\n"
        "\n"
        "    def put(self, key, value):\n"
        "        self._store[key] = value\n"
    ))
    patch = generate_patch(tmp_path, hyp({
        "kind": "shared_mutable_class_attr", "file": "cache.py",
        "class": "Cache", "attr": "_store",
    }))
    assert patch is not None
    apply_unified_diff(tmp_path, patch.diff)
    text = (tmp_path / "cache.py").read_text(encoding="utf-8")
    scope = {}
    exec(text, scope)
    a, b = scope["Cache"](), scope["Cache"]()
    a.put("k", 1)
    assert not hasattr(type(b), "_store") or b._store == {}
    assert b._store is not a._store


def test_patch_shared_class_attr_without_init(tmp_path):
    write(tmp_path, "cache.py", (
        "class Cache:\n"
        "    _store = {}\n"
        "\n"
        "    def put(self, key, value):\n"
        "        self._store[key] = value\n"
        "\n"
        "    def get(self, key):\n"
        "        return self._store.get(key)\n"
    ))
    patch = generate_patch(tmp_path, hyp({
        "kind": "shared_mutable_class_attr", "file": "cache.py",
        "class": "Cache", "attr": "_store",
    }))
    assert patch is not None
    apply_unified_diff(tmp_path, patch.diff)
    scope = {}
    exec((tmp_path / "cache.py").read_text(encoding="utf-8"), scope)
    a, b = scope["Cache"](), scope["Cache"]()
    a.put("k", 1)
    assert b.get("k") is None


def test_patch_schema_key(tmp_path):
    write(tmp_path, "users.py", (
        "def get_user():\n"
        "    return {'user_name': 'ada', 'id': 1}\n"
    ))
    patch = generate_patch(tmp_path, hyp({
        "kind": "schema_key_mismatch", "file": "users.py", "function": "get_user",
        "old_key": "user_name", "new_key": "username", "line": 2,
    }))
    assert patch is not None
    assert patch.lines_changed == 2  # one removed + one added
    apply_unified_diff(tmp_path, patch.diff)
    scope = {}
    exec((tmp_path / "users.py").read_text(encoding="utf-8"), scope)
    assert scope["get_user"]() == {"username": "ada", "id": 1}


def test_patch_unknown_kind_returns_none(tmp_path):
    assert generate_patch(tmp_path, hyp({"kind": "none"})) is None


# --------------------------------------------------------------------- #
# applier
# --------------------------------------------------------------------- #

DIFF = """\
--- a/greet.py
+++ b/greet.py
@@ -1,3 +1,3 @@
 def greet(name):
-    return 'hello ' + nane
+    return 'hello ' + name

"""


def test_apply_unified_diff(tmp_path):
    write(tmp_path, "greet.py", "def greet(name):\n    return 'hello ' + nane\n\n")
    changed = apply_unified_diff(tmp_path, DIFF)
    assert changed == ["greet.py"]
    assert "nane" not in (tmp_path / "greet.py").read_text(encoding="utf-8")


def test_apply_context_mismatch_raises_and_leaves_tree(tmp_path):
    original = "completely different content\n"
    write(tmp_path, "greet.py", original)
    with pytest.raises(PatchError):
        apply_unified_diff(tmp_path, DIFF)
    assert (tmp_path / "greet.py").read_text(encoding="utf-8") == original


def test_apply_missing_file_raises(tmp_path):
    with pytest.raises(PatchError):
        apply_unified_diff(tmp_path, DIFF)


def test_parse_rejects_garbage():
    with pytest.raises(PatchError):
        parse_unified_diff("not a diff at all")
