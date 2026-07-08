"""Docker-based executor: the preferred isolation boundary.

Each command runs in a fresh container from a pinned runner image with the
network disabled and memory/CPU caps. The workspace directory is bind-mounted
read-write at /workspace (the workspace itself is already a disposable copy).
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from repomedic.execute.base import DEFAULT_TIMEOUT_S
from repomedic.models.execution import CommandResult

RUNNER_IMAGE = "repomedic-runner:py312"

RUNNER_DOCKERFILE = """\
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest==8.* && useradd -m runner
USER runner
WORKDIR /workspace
"""


class DockerExecutor:
    name = "docker"

    def __init__(self, image: str = RUNNER_IMAGE, memory: str = "1g", cpus: str = "1") -> None:
        self.image = image
        self.memory = memory
        self.cpus = cpus

    def available(self) -> bool:
        try:
            proc = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True, text=True, timeout=15,
            )
            return proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def ensure_image(self) -> None:
        """Build the runner image if it is not present locally."""
        probe = subprocess.run(
            ["docker", "image", "inspect", self.image],
            capture_output=True, text=True, timeout=30,
        )
        if probe.returncode == 0:
            return
        build = subprocess.run(
            ["docker", "build", "-t", self.image, "-f", "-", "."],
            input=RUNNER_DOCKERFILE,
            capture_output=True, text=True, timeout=600,
        )
        if build.returncode != 0:
            raise RuntimeError(f"failed to build runner image:\n{build.stderr[-2000:]}")

    def run(
        self, workdir: Path, command: list[str], timeout_s: int = DEFAULT_TIMEOUT_S
    ) -> CommandResult:
        self.ensure_image()
        mount = str(Path(workdir).resolve())
        docker_cmd = [
            "docker", "run", "--rm",
            "--network", "none",
            "--memory", self.memory,
            "--cpus", self.cpus,
            "--pids-limit", "512",
            "-v", f"{mount}:/workspace",
            "-w", "/workspace",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            self.image,
            *command,
        ]
        start = time.monotonic()
        try:
            proc = subprocess.run(
                docker_cmd,
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
        except subprocess.TimeoutExpired:
            return CommandResult(
                command=command,
                exit_code=-1,
                stderr="command timed out (container may need manual cleanup)",
                duration_s=time.monotonic() - start,
                timed_out=True,
                executor=self.name,
                cwd=str(workdir),
            )


def select_executor(prefer: str = "auto"):
    """Pick the executor: docker when the daemon answers, local otherwise."""
    from repomedic.execute.local_executor import LocalExecutor

    if prefer == "local":
        return LocalExecutor()
    docker = DockerExecutor()
    if prefer == "docker":
        if not docker.available():
            raise RuntimeError("docker requested but the daemon is not reachable")
        return docker
    return docker if docker.available() else LocalExecutor()
