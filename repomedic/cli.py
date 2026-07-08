"""RepoMedic CLI: `repomedic investigate ./broken-repo`."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from repomedic import __version__
from repomedic.events import ConsoleSink, EventBus
from repomedic.execute.docker_executor import select_executor
from repomedic.investigate.heuristics import DeterministicInvestigator
from repomedic.models.investigation import InvestigationSession
from repomedic.store.db import SessionStore, SQLiteSink


def _store_for(repo_path: Path) -> SessionStore:
    return SessionStore(repo_path / ".repomedic" / "repomedic.db")


@click.group()
@click.version_option(version=__version__, prog_name="repomedic")
def main() -> None:
    """RepoMedic — autonomous repository failure investigation."""


@main.command()
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--executor", "executor_pref",
              type=click.Choice(["auto", "docker", "local"]), default="auto",
              show_default=True,
              help="Isolation backend. auto = Docker when the daemon answers.")
@click.option("--provider", "provider_name",
              type=click.Choice(["deterministic"]), default="deterministic",
              show_default=True,
              help="AgentProvider driving the investigation (LLM providers plug "
                   "in behind the same interface).")
@click.option("--timeout", "timeout_s", type=int, default=300, show_default=True,
              help="Per-command timeout in seconds.")
@click.option("--verbose", "-v", is_flag=True, help="Also print state transitions.")
def investigate(repo_path: Path, executor_pref: str, provider_name: str,
                timeout_s: int, verbose: bool) -> None:
    """Investigate failing tests in REPO_PATH and propose a validated patch."""
    from repomedic.engine import InvestigationEngine

    repo_path = repo_path.resolve()
    try:
        executor = select_executor(executor_pref)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    store = _store_for(repo_path)
    session_id = store.next_session_id()
    session = InvestigationSession(
        session_id=session_id, repo_path=str(repo_path), executor=executor.name,
    )
    bus = EventBus(session_id)
    bus.subscribe(ConsoleSink(echo=click.echo, verbose=verbose))
    bus.subscribe(SQLiteSink(store))

    provider = DeterministicInvestigator(repo_root=repo_path)
    engine = InvestigationEngine(
        repo_path=repo_path, provider=provider, executor=executor,
        bus=bus, session=session, timeout_s=timeout_s,
    )
    session = engine.run()
    store.save_session(session)

    if session.state == "COMPLETE" and session.validation \
            and session.validation.verdict == "accepted":
        sys.exit(0)
    elif session.state == "NO_FAILURE":
        sys.exit(0)
    else:
        sys.exit(1)


@main.command("sessions")
@click.argument("repo_path", type=click.Path(exists=True, file_okay=False, path_type=Path),
                default=".")
def sessions(repo_path: Path) -> None:
    """List stored investigation sessions for REPO_PATH."""
    store = _store_for(repo_path.resolve())
    rows = store.list_sessions()
    if not rows:
        click.echo("no sessions recorded")
        return
    for row in rows:
        click.echo(f"{row['session_id']}  {row['created_at']}  "
                   f"{row['state']:<12} {row['repo_path']}")


@main.command("show")
@click.argument("session_id")
@click.option("--repo", "repo_path",
              type=click.Path(exists=True, file_okay=False, path_type=Path), default=".")
def show(session_id: str, repo_path: Path) -> None:
    """Print the stored event stream of a session."""
    store = _store_for(repo_path.resolve())
    events = store.events_for_session(session_id)
    if not events:
        raise click.ClickException(f"no events for {session_id}")
    for event in events:
        click.echo(f"[{event['stage']}] {event['message']}")


@main.command("dashboard")
@click.option("--repo", "repo_path",
              type=click.Path(exists=True, file_okay=False, path_type=Path), default=".")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8787, show_default=True)
def dashboard(repo_path: Path, host: str, port: int) -> None:
    """Serve the minimal web dashboard for stored sessions."""
    try:
        import uvicorn

        from repomedic.dashboard.app import create_app
    except ImportError as exc:
        raise click.ClickException(
            "dashboard extras not installed: pip install 'repomedic[dashboard]'"
        ) from exc
    app = create_app(_store_for(repo_path.resolve()))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
