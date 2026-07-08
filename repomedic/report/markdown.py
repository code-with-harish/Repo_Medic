"""Render an investigation session as a Markdown incident report."""

from __future__ import annotations

from repomedic.events import Event
from repomedic.models.investigation import InvestigationSession


def _h(text: str, level: int = 2) -> str:
    return f"{'#' * level} {text}\n"


def render(session: InvestigationSession, events: list[Event] | None = None) -> str:
    out: list[str] = []
    out.append(f"# RepoMedic Incident Report — {session.session_id}\n")
    out.append(f"- **Repository:** `{session.repo_path}`")
    out.append(f"- **Date:** {session.created_at.strftime('%Y-%m-%d %H:%M UTC')}")
    out.append(f"- **Final state:** `{session.state}`")
    out.append(f"- **Executor:** `{session.executor}`")
    if session.error:
        out.append(f"- **Error:** {session.error}")
    out.append("")

    # ---------------------------------------------------------------- #
    out.append(_h("Failure summary"))
    execution = session.initial_execution
    if execution is None:
        out.append("Tests were never executed.\n")
    elif execution.all_green:
        out.append(f"All {execution.total} tests passed — nothing to investigate.\n")
    else:
        out.append(
            f"{execution.failed + execution.errors} of {execution.total} tests "
            f"failed ({execution.passed} passed, {execution.skipped} skipped).\n"
        )
        for failure in execution.failures:
            out.append(f"- `{failure.test_id}` — **{failure.exception_type or 'failure'}**: "
                       f"{failure.message}")
        out.append("")

    # ---------------------------------------------------------------- #
    out.append(_h("Reproduction steps"))
    if session.repo:
        cmd = " ".join(session.repo.test_command) or "python -m pytest -q"
        out.append("```bash")
        out.append(f"cd {session.repo_path}")
        out.append(cmd)
        out.append("```\n")
        out.append(
            f"Detected: language `{session.repo.language}`, package manager "
            f"`{session.repo.package_manager}`, test framework "
            f"`{session.repo.test_framework}`, {session.repo.module_count} modules, "
            f"{len(session.repo.edges)} import edges.\n"
        )

    # ---------------------------------------------------------------- #
    if events:
        out.append(_h("Investigation timeline"))
        out.append("| # | Stage | Event |")
        out.append("|---|-------|-------|")
        for event in events:
            message = event.message.replace("|", "\\|")
            out.append(f"| {event.seq} | `{event.stage}` | {message} |")
        out.append("")

    # ---------------------------------------------------------------- #
    if session.hypotheses:
        out.append(_h("Hypotheses considered"))
        for hyp in session.hypotheses:
            out.append(f"### {hyp.hypothesis_id} — {hyp.category} "
                       f"(confidence {hyp.confidence:.2f}, status {hyp.status.value})\n")
            out.append(f"{hyp.description}\n")
            if hyp.suspect:
                out.append(f"- **Suspect:** `{hyp.suspect.file}:{hyp.suspect.line}` "
                           f"(`{hyp.suspect.symbol}`)")
            out.append(f"- **Prior:** {hyp.prior:.2f}")
            if hyp.supporting_evidence:
                out.append(f"- **Supporting evidence:** "
                           f"{', '.join(hyp.supporting_evidence)}")
            if hyp.contradicting_evidence:
                out.append(f"- **Contradicting evidence:** "
                           f"{', '.join(hyp.contradicting_evidence)}")
            if hyp.experiment:
                out.append(f"- **Experiment:** {hyp.experiment.description} — "
                           f"verdict: `{hyp.experiment.verdict or 'not run'}`")
            out.append("")

    # ---------------------------------------------------------------- #
    if session.evidence:
        out.append(_h("Evidence log"))
        for evidence in session.evidence.values():
            out.append(f"- **{evidence.evidence_id}** ({evidence.kind.value}): "
                       f"{evidence.description}")
        out.append("")

    # ---------------------------------------------------------------- #
    out.append(_h("Root cause"))
    if session.root_cause:
        rc = session.root_cause
        out.append(
            f"**`{rc.file}:{rc.line}`** — {rc.description}\n\n"
            f"Selected from hypothesis **{rc.hypothesis_id}** with confidence "
            f"**{rc.confidence:.2f}**, backed by evidence "
            f"{', '.join(rc.evidence_ids)}.\n"
        )
    else:
        out.append("No root cause met the evidence-backed confidence threshold.\n")

    # ---------------------------------------------------------------- #
    if session.patch:
        out.append(_h("Patch"))
        out.append(f"{session.patch.description} "
                   f"({session.patch.lines_changed} changed lines)\n")
        out.append("```diff")
        out.append(session.patch.diff.rstrip("\n"))
        out.append("```\n")

    # ---------------------------------------------------------------- #
    out.append(_h("Validation results"))
    validation = session.validation
    if validation is None:
        out.append("No patch was validated.\n")
    else:
        original = "PASS" if validation.original_failures_passed else "FAIL"
        out.append(f"- Original failing tests after patch: **{original}**")
        if validation.regression_run is not None:
            regression = "PASS" if validation.regression_passed else "FAIL"
            out.append(f"- Full regression suite ({validation.regression_total} tests): "
                       f"**{regression}**")
        out.append(f"- Verdict: **`{validation.verdict}`**\n")

    out.append("---")
    out.append("_Generated by RepoMedic. A root cause is only reported with recorded "
               "evidence; a patch is only accepted when validation commands actually "
               "passed._")
    return "\n".join(out) + "\n"
