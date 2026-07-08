import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from repomedic.cli import main

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.mark.e2e
def test_cli_investigate_cache_bug(tmp_path):
    repo = tmp_path / "cache-bug"
    shutil.copytree(FIXTURES / "cache-bug", repo,
                    ignore=shutil.ignore_patterns(".repomedic", "__pycache__"))
    runner = CliRunner()
    result = runner.invoke(
        main, ["investigate", str(repo), "--executor", "local"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    out = result.output
    assert "[INGEST] Repository detected: Python / pytest" in out
    assert "[FAILURE]" in out
    assert "[ROOT_CAUSE] src/cache.py:" in out
    assert "[VALIDATE] Original failures: PASS" in out
    assert "[REGRESSION] 7 tests: PASS" in out
    assert "[REPORT] .repomedic/reports/session-001.md" in out
    assert (repo / ".repomedic" / "reports" / "session-001.md").exists()
    assert (repo / ".repomedic" / "repomedic.db").exists()

    # sessions + show read back from the store
    listed = runner.invoke(main, ["sessions", str(repo)], catch_exceptions=False)
    assert "session-001" in listed.output
    shown = runner.invoke(main, ["show", "session-001", "--repo", str(repo)],
                          catch_exceptions=False)
    assert "[ROOT_CAUSE]" in shown.output


@pytest.mark.e2e
def test_cli_exit_code_on_inconclusive(tmp_path):
    repo = tmp_path / "mystery"
    repo.mkdir()
    (repo / "notes.txt").write_text("no code here", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, ["investigate", str(repo), "--executor", "local"])
    assert result.exit_code == 1
