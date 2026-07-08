"""AgentProvider interface.

The investigation engine is provider-agnostic: anything that can look at the
observed failures plus the repository model and propose hypotheses, and later
interpret experiment output, can drive an investigation. The deterministic
heuristic provider ships with RepoMedic (demo mode, CI); an LLM-backed
provider can be plugged in behind the same interface.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from repomedic.models.execution import ExecutionResult
from repomedic.models.investigation import Hypothesis, PatchProposal
from repomedic.models.repo import RepoModel


@runtime_checkable
class AgentProvider(Protocol):
    """Strategy interface for hypothesis generation and patch proposal."""

    name: str

    def generate_hypotheses(
        self, repo: RepoModel, execution: ExecutionResult
    ) -> list[Hypothesis]:
        """Propose candidate root causes for the observed failures.

        Each hypothesis must carry a prior confidence, a suspect location when
        one can be identified, and (when verifiable) an experiment script.
        """
        ...

    def propose_patch(
        self, repo: RepoModel, hypothesis: Hypothesis
    ) -> PatchProposal | None:
        """Build a minimal patch for a verified hypothesis, or None."""
        ...
