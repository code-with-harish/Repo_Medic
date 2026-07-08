"""Apply unified diffs to a working copy.

A small, dependency-free unified-diff engine (no `patch` binary on Windows,
no GitPython). Context lines are verified before any file is written; a
mismatch raises `PatchError` and leaves the tree untouched.

Known normalization: files are read with universal newlines and written back
with LF endings, so patching a CRLF file rewrites it with LF line endings
(content is otherwise preserved). Lines added by a hunk always end with a
newline. Both are acceptable for the generator/applier pair RepoMedic uses
internally and are covered by tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(RuntimeError):
    pass


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)  # includes leading ' ', '+', '-'


@dataclass
class FilePatch:
    old_path: str
    new_path: str
    hunks: list[Hunk] = field(default_factory=list)


def parse_unified_diff(diff_text: str) -> list[FilePatch]:
    patches: list[FilePatch] = []
    current: FilePatch | None = None
    hunk: Hunk | None = None
    lines = diff_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            if i + 1 >= len(lines) or not lines[i + 1].startswith("+++ "):
                raise PatchError(f"malformed file header at line {i + 1}")
            old_path = line[4:].strip()
            new_path = lines[i + 1][4:].strip()
            current = FilePatch(old_path=old_path, new_path=new_path)
            patches.append(current)
            hunk = None
            i += 2
            continue
        match = HUNK_RE.match(line)
        if match:
            if current is None:
                raise PatchError("hunk before file header")
            hunk = Hunk(
                old_start=int(match[1]),
                old_count=int(match[2] or "1"),
                new_start=int(match[3]),
                new_count=int(match[4] or "1"),
            )
            current.hunks.append(hunk)
            i += 1
            continue
        if hunk is not None and (line[:1] in (" ", "+", "-") or line == ""):
            hunk.lines.append(line if line else " ")
            i += 1
            continue
        if line.startswith("\\ No newline"):
            i += 1
            continue
        # Anything else between files (e.g. "diff --git" headers) is skipped.
        i += 1
    if not patches:
        raise PatchError("no file patches found in diff")
    return patches


def _strip_prefix(path: str) -> str:
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def render_file_patch(root: Path, fp: FilePatch) -> tuple[str, str]:
    """Compute the patched text for one file without writing. Returns (rel, text)."""
    rel = _strip_prefix(fp.new_path)
    target = Path(root) / rel
    if not target.exists():
        raise PatchError(f"target file does not exist: {rel}")
    original = target.read_text(encoding="utf-8").splitlines(keepends=True)

    result: list[str] = []
    cursor = 0  # index into original (0-based)
    for hunk in fp.hunks:
        hunk_start = hunk.old_start - 1
        if hunk_start < cursor:
            raise PatchError(f"overlapping hunks in {rel}")
        result.extend(original[cursor:hunk_start])
        cursor = hunk_start
        for hline in hunk.lines:
            tag, content = hline[:1], hline[1:]
            if tag == " ":
                if cursor >= len(original) or original[cursor].rstrip("\r\n") != content.rstrip("\r\n"):
                    found = original[cursor].rstrip("\r\n") if cursor < len(original) else "<eof>"
                    raise PatchError(
                        f"context mismatch in {rel} at line {cursor + 1}: "
                        f"expected {content!r}, found {found!r}"
                    )
                result.append(original[cursor])
                cursor += 1
            elif tag == "-":
                if cursor >= len(original) or original[cursor].rstrip("\r\n") != content.rstrip("\r\n"):
                    found = original[cursor].rstrip("\r\n") if cursor < len(original) else "<eof>"
                    raise PatchError(
                        f"delete mismatch in {rel} at line {cursor + 1}: "
                        f"expected {content!r}, found {found!r}"
                    )
                cursor += 1
            elif tag == "+":
                result.append(content + "\n")
            else:
                raise PatchError(f"unknown hunk line tag {tag!r} in {rel}")
    result.extend(original[cursor:])
    return rel, "".join(result)


def apply_unified_diff(root: Path, diff_text: str) -> list[str]:
    """Verify and apply a unified diff under `root`. Returns changed files.

    Every file is patched in memory first; nothing is written until all hunks
    of all files verified, so a context mismatch cannot leave the tree
    half-patched."""
    root = Path(root)
    patches = parse_unified_diff(diff_text)
    staged = [render_file_patch(root, fp) for fp in patches]
    for rel, text in staged:
        (root / rel).write_text(text, encoding="utf-8", newline="")
    return [rel for rel, _ in staged]
