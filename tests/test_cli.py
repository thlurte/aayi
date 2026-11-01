"""CLI functional tests using Typer CliRunner."""

from pathlib import Path
from typer.testing import CliRunner

from aayi.cli.main import app

runner = CliRunner()


def test_cli_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "Aayi version 0.1.0" in result.stdout


def test_cli_init_generates_files(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0

    assert (tmp_path / "config" / "deploy.yml").is_file()
    assert (tmp_path / "Dockerfile").is_file()
    assert (tmp_path / ".dockerignore").is_file()

    # Second invocation without --force skips existing files
    second_result = runner.invoke(app, ["init"])
    assert second_result.exit_code == 0
    assert "already exists" in second_result.stdout


def test_cli_ci_init(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["ci", "init"])
    assert result.exit_code == 0
    workflow = tmp_path / ".github" / "workflows" / "deploy.yml"
    assert workflow.is_file()
    content = workflow.read_text(encoding="utf-8")
    assert "uvx aayi deploy" in content
    assert "astral-sh/setup-uv" in content

