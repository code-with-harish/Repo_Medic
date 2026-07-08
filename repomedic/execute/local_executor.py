"""Subprocess-based executor.

Fallback when no Docker daemon is available, and the deterministic engine for
the test suite. Commands run in the disposable workspace copy with a scrubbed
environment and a hard timeout — isolation is best-effort (no syscall/network
sandboxing); see the safety notes in the README.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from repomedic.execute.base import DEFAULT_TIMEOUT_S
from repomedic.models.execution import CommandResult

# Environment variables the child process legitimately needs on each platform.
_KEEP_ENV = {
    "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "WINDIR", "TEMP", "TMP",
    "HOME", "USERPROFILE", "LANG", "LC_ALL", "PATHEXT", "PROCESSOR_ARCHITECTURE",
}


class LocalExecutor:
    name = "local"

    def available(self) -> bool:
        return True

    def run(
        self, workdir: Path, command: list[str], timeout_s: int = DEFAULT_TIMEOUT_S
    ) -> CommandResult:
        resolved = [sys.executable if part == "python" else part for part in command]
        env = {k: v for k, v in os.environ.items() if k.upper() in _KEEP_ENV}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        start = time.monotonic()
        try:
            proc = subprocess.run(
                resolved,
                cwd=str(workdir),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
            )
            return CommandResult(
                command=command,
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration_s=time.monotonic() - start,
                executor=self.name,
                cwd=str(workdir),
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                command=command,
                exit_code=-1,
                stdout=(exc.stdout or b"").decode("utf-8", "replace")
                if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                stderr="command timed out",
                duration_s=time.monotonic() - start,
                timed_out=True,
                executor=self.name,
                cwd=str(workdir),
            )
        except FileNotFoundError as exc:
            return CommandResult(
                command=command,
                exit_code=127,
                stderr=str(exc),
                duration_s=time.monotonic() - start,
                executor=self.name,
                cwd=str(workdir),
            )
