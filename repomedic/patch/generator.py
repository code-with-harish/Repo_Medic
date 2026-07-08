"""Generate minimal unified-diff patches from verified hypotheses.

Each hypothesis category has a deterministic, AST-guided patch template.
The generator never touches the repository: it reads the source, computes the
patched text, and returns a unified diff for the applier/validator to use in
a temporary working copy.
"""

from __future__ import annotations

import ast
import difflib
from pathlib import Path

from repomedic.models.investigation import Hypothesis, PatchProposal

MUTABLE_FACTORY = {"[]": "[]", "{}": "{}"}


def _unified_diff(rel_path: str, before: str, after: str) -> str:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
    )
    return "".join(diff)


def _count_changed(diff: str) -> int:
    return sum(
        1 for line in diff.splitlines()
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith(("+++", "---"))
    )


def _find_function(tree: ast.Module, qualname: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    parts = qualname.split(".")
    scope: list[ast.stmt] = tree.body
    node: ast.AST | None = None
    for i, part in enumerate(parts):
        node = None
        for item in scope:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and item.name == part:
                node = item
                break
        if node is None:
            return None
        if i < len(parts) - 1:
            if not isinstance(node, ast.ClassDef):
                return None
            scope = node.body
    return node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None


def generate_patch(repo_root: Path, hypothesis: Hypothesis) -> PatchProposal | None:
    ctx = hypothesis.patch_context
    kind = ctx.get("kind")
    if kind == "mutable_default_argument":
        return _patch_mutable_default(repo_root, ctx)
    if kind == "shared_mutable_class_attr":
        return _patch_shared_class_attr(repo_root, ctx)
    if kind == "schema_key_mismatch":
        return _patch_schema_key(repo_root, ctx)
    return None


def _read(repo_root: Path, rel: str) -> tuple[str, list[str]] | None:
    path = Path(repo_root) / rel
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text, text.splitlines(keepends=True)


def _patch_mutable_default(repo_root: Path, ctx: dict) -> PatchProposal | None:
    """`def f(x=[])` -> `def f(x=None)` plus an idiom guard at body start."""
    loaded = _read(repo_root, ctx["file"])
    if loaded is None:
        return None
    text, lines = loaded
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    func = _find_function(tree, ctx["qualname"])
    if func is None:
        return None

    # Map each flagged arg to its default node.
    positional = func.args.posonlyargs + func.args.args
    pos_defaults = list(zip(positional[len(positional) - len(func.args.defaults):],
                            func.args.defaults, strict=True))
    kw_defaults = [(a, d) for a, d in zip(func.args.kwonlyargs, func.args.kw_defaults,
                                          strict=True) if d is not None]
    replacements: list[tuple[ast.expr, str]] = []  # (default node, original source)
    guards: list[tuple[str, str]] = []  # (arg name, factory expression)
    for arg, default in pos_defaults + kw_defaults:
        if arg.arg in ctx["args"]:
            segment = ast.get_source_segment(text, default) or ""
            replacements.append((default, segment))
            guards.append((arg.arg, segment if segment in ("[]", "{}", "dict()", "list()", "set()")
                           else segment))

    if not replacements:
        return None
    # Only single-line signatures are handled by this template.
    for default, _ in replacements:
        if default.lineno != default.end_lineno:
            return None

    # Replace default expressions with None, right-to-left to keep offsets valid.
    for default, _segment in sorted(replacements, key=lambda r: r[0].col_offset, reverse=True):
        row = default.lineno - 1
        line = lines[row]
        lines[row] = line[: default.col_offset] + "None" + line[default.end_col_offset:]

    # Insert guards at the top of the body, preserving indentation.
    body_first = func.body[0]
    indent = " " * body_first.col_offset
    guard_lines = []
    for arg_name, factory in guards:
        guard_lines.append(f"{indent}if {arg_name} is None:\n")
        guard_lines.append(f"{indent}    {arg_name} = {factory}\n")
    insert_at = body_first.lineno - 1
    lines[insert_at:insert_at] = guard_lines

    after = "".join(lines)
    diff = _unified_diff(ctx["file"], text, after)
    return PatchProposal(
        diff=diff,
        files=[ctx["file"]],
        description=(
            f"replace mutable default(s) {ctx['args']} of `{ctx['qualname']}` with "
            f"None + in-body initialization"
        ),
        lines_changed=_count_changed(diff),
    )


def _patch_shared_class_attr(repo_root: Path, ctx: dict) -> PatchProposal | None:
    """Move a mutable class attribute into per-instance state in __init__."""
    loaded = _read(repo_root, ctx["file"])
    if loaded is None:
        return None
    text, lines = loaded
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None

    cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == ctx["class"]),
        None,
    )
    if cls is None:
        return None
    attr_stmt = None
    for item in cls.body:
        if isinstance(item, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == ctx["attr"] for t in item.targets
        ):
            attr_stmt = item
            break
        if (isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
                and item.target.id == ctx["attr"] and item.value is not None):
            attr_stmt = item
            break
    if attr_stmt is None or attr_stmt.lineno != attr_stmt.end_lineno:
        return None
    value_src = ast.get_source_segment(text, attr_stmt.value) or "{}"
    attr_indent = " " * attr_stmt.col_offset

    init = next(
        (m for m in cls.body if isinstance(m, ast.FunctionDef) and m.name == "__init__"),
        None,
    )

    new_lines = list(lines)
    if init is not None:
        body_first = init.body[0]
        indent = " " * body_first.col_offset
        insertion = f"{indent}self.{ctx['attr']} = {value_src}\n"
        insert_at = body_first.lineno - 1
        new_lines[insert_at:insert_at] = [insertion]
        remove_at = attr_stmt.lineno - 1 + (1 if insert_at <= attr_stmt.lineno - 1 else 0)
        del new_lines[remove_at]
    else:
        # Replace the attribute line with a fresh __init__.
        remove_at = attr_stmt.lineno - 1
        block = [
            f"{attr_indent}def __init__(self):\n",
            f"{attr_indent}    self.{ctx['attr']} = {value_src}\n",
        ]
        new_lines[remove_at:remove_at + 1] = block

    after = "".join(new_lines)
    diff = _unified_diff(ctx["file"], text, after)
    return PatchProposal(
        diff=diff,
        files=[ctx["file"]],
        description=(
            f"move shared mutable class attribute `{ctx['class']}.{ctx['attr']}` "
            f"into per-instance state"
        ),
        lines_changed=_count_changed(diff),
    )


def _patch_schema_key(repo_root: Path, ctx: dict) -> PatchProposal | None:
    """Rename the producer's dict key to the one the contract (tests) expects."""
    loaded = _read(repo_root, ctx["file"])
    if loaded is None:
        return None
    text, lines = loaded
    row = ctx["line"] - 1
    if row >= len(lines):
        return None
    line = lines[row]
    old, new = ctx["old_key"], ctx["new_key"]
    for quote in ("'", '"'):
        needle = f"{quote}{old}{quote}"
        if needle in line:
            lines[row] = line.replace(needle, f"{quote}{new}{quote}", 1)
            break
    else:
        return None

    after = "".join(lines)
    diff = _unified_diff(ctx["file"], text, after)
    return PatchProposal(
        diff=diff,
        files=[ctx["file"]],
        description=(
            f"rename key '{old}' to '{new}' in `{ctx['function']}` "
            f"({ctx['file']}:{ctx['line']}) to restore the expected schema"
        ),
        lines_changed=_count_changed(diff),
    )
