"""CLI functional tests using Typer CliRunner."""

from pathlib import Path
from typer.testing import CliRunner

from aayi import __version__
from aayi.cli.main import app

runner = CliRunner()


def test_cli_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"Aayi version {__version__}" in result.stdout


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


def test_cli_overrides_helper():
    from aayi.cli.main import _apply_cli_overrides
    from aayi.config.models import DeployConfig, ServerConfig, ServiceDefinition

    cfg = DeployConfig(
        service="web",
        image="myapp:latest",
        servers=ServerConfig(web=["1.2.3.4"]),
    )
    assert cfg.services["web"].public is False

    # Make public with custom subdomain from CLI
    _apply_cli_overrides(cfg, domain="custom.example.com", public=True)
    assert cfg.services["web"].public is True
    assert cfg.services["web"].domain == "custom.example.com"
    assert cfg.proxy.domain == "custom.example.com"

    # Make private from CLI
    _apply_cli_overrides(cfg, private=True)
    assert cfg.services["web"].public is False

