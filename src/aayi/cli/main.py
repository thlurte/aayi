"""Aayi CLI entrypoint powered by Typer and Rich."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from aayi import __version__
from aayi.cli.ci import init_github_actions
from aayi.cli.init import init_project
from aayi.compose.parser import convert_compose_to_deploy_yaml, parse_compose_file
from aayi.config.loader import load_config
from aayi.orchestrator.accessories import AccessoryManager
from aayi.orchestrator.deployer import Deployer
from aayi.orchestrator.rollback import RollbackManager
from aayi.proxy.caddy import CaddyManager
from aayi.ssh.runner import SSHFleetRunner

app = typer.Typer(
    name="aayi",
    help="Modern Python container deployment orchestrator with zero-downtime Caddy proxying.",
    add_completion=True,
    rich_markup_mode="rich",
)
proxy_app = typer.Typer(help="Manage Caddy reverse proxy on remote hosts.")
accessory_app = typer.Typer(help="Manage attached stateful accessories (Postgres, Redis, etc.).")
ci_app = typer.Typer(help="Manage CI/CD workflows (GitHub Actions, etc.).")
compose_app = typer.Typer(help="Docker Compose file management and conversion.")

app.add_typer(proxy_app, name="proxy")
app.add_typer(accessory_app, name="accessory")
app.add_typer(ci_app, name="ci")
app.add_typer(compose_app, name="compose")

console = Console()


def _apply_cli_overrides(
    cfg: DeployConfig,
    target_service: Optional[str] = None,
    domain: Optional[str] = None,
    public: Optional[bool] = None,
    private: Optional[bool] = None,
) -> None:
    """Apply CLI overrides for domain, public, and private flags onto DeployConfig."""
    is_private = private if private is not None else (False if public is True else None)
    is_public = public if public is not None else (False if private is True else None)

    # Determine services to update
    targets = [target_service] if target_service else list(cfg.services.keys())

    for name in targets:
        if name in cfg.services:
            srv = cfg.services[name]
            if is_private is True:
                srv.public = False
            elif is_public is True:
                srv.public = True
            if domain:
                srv.domain = domain
                srv.domains = [domain]
                srv.public = True

    # Also update global proxy domain if applicable
    if domain:
        cfg.proxy.domain = domain
        cfg.proxy.domains = [domain]


def _build_runner(cfg: DeployConfig) -> SSHFleetRunner:
    """Build an SSHFleetRunner from DeployConfig with all SSH keys, password, and passphrase."""
    return SSHFleetRunner(
        default_user=cfg.servers.user,
        default_port=cfg.servers.ssh_port,
        default_key=cfg.servers.ssh_key,
        default_keys=cfg.servers.ssh_keys,
        password=cfg.servers.ssh_password,
        passphrase=cfg.servers.passphrase,
    )


@app.command()
def init(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing configuration files."),
    ci: bool = typer.Option(False, "--ci", help="Also generate GitHub Actions deployment workflow."),
) -> None:
    """Initialize Aayi deployment configuration (config/deploy.yml) and Dockerfile."""
    init_project(force=force)
    if ci:
        init_github_actions(force=force)


@ci_app.command("init")
def ci_init(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing workflow files."),
) -> None:
    """Generate .github/workflows/deploy.yml for GitHub Actions CI/CD."""
    init_github_actions(force=force)


@app.command()
def setup(
    config_path: Path = typer.Option("config/deploy.yml", "--config", "-c", help="Path to deploy.yml"),
) -> None:
    """Prepare target servers: verify Docker CE and start Caddy reverse proxy."""
    cfg = load_config(config_path)
    runner = _build_runner(cfg)
    deployer = Deployer(cfg)

    async def _run() -> None:
        console.rule("[bold blue]Setting up target hosts[/bold blue]")
        await deployer.remote_docker.ensure_docker(cfg.servers.all_hosts)
        if cfg.servers.web:
            await deployer.caddy.ensure_running(cfg.servers.web)
        console.rule("[bold green]✓ Server setup complete[/bold green]")

    asyncio.run(_run())


@app.command()
def deploy(
    config_path: Path = typer.Option("config/deploy.yml", "--config", "-c", help="Path to deploy.yml"),
    compose: Optional[Path] = typer.Option(None, "--compose", help="Deploy directly from a docker-compose.yml file"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Deploy only a specific service container"),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Custom image tag / git revision to deploy"),
    domain: Optional[str] = typer.Option(None, "--domain", "-d", help="Override public domain/subdomain from CLI (e.g. api.example.com)"),
    public: Optional[bool] = typer.Option(None, "--public", help="Force container to be publicly accessible with domain/TLS"),
    private: Optional[bool] = typer.Option(None, "--private", help="Force container to be private (isolated to aayi-net with no exposed ports)"),
    build: bool = typer.Option(True, "--build/--no-build", help="Build image locally before deployment"),
    push: bool = typer.Option(True, "--push/--no-push", help="Push image to registry after building"),
    remote_build: bool = typer.Option(False, "--remote-build", "-r", help="Stream source in memory via tar and build natively on the VPS (no registry needed)."),
) -> None:
    """Run full zero-downtime deployment: build, push, healthcheck, and swap Caddy traffic."""
    if compose:
        cfg = parse_compose_file(compose)
    else:
        cfg = load_config(config_path)

    # Apply CLI visibility and domain overrides
    _apply_cli_overrides(cfg, target_service=service, domain=domain, public=public, private=private)

    deployer = Deployer(cfg)
    asyncio.run(deployer.deploy(tag=tag, build=build, push=push, target_service=service, remote_build=remote_build))


@app.command()
def redeploy(
    config_path: Path = typer.Option("config/deploy.yml", "--config", "-c", help="Path to deploy.yml"),
    compose: Optional[Path] = typer.Option(None, "--compose", help="Deploy directly from a docker-compose.yml file"),
    service: Optional[str] = typer.Option(None, "--service", "-s", help="Redeploy only a specific service container"),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Tag to redeploy without rebuilding"),
    domain: Optional[str] = typer.Option(None, "--domain", "-d", help="Override public domain/subdomain from CLI (e.g. api.example.com)"),
    public: Optional[bool] = typer.Option(None, "--public", help="Force container to be publicly accessible with domain/TLS"),
    private: Optional[bool] = typer.Option(None, "--private", help="Force container to be private (isolated to aayi-net with no exposed ports)"),
) -> None:
    """Redeploy an existing image tag on remote servers without rebuilding."""
    if compose:
        cfg = parse_compose_file(compose)
    else:
        cfg = load_config(config_path)

    # Apply CLI visibility and domain overrides
    _apply_cli_overrides(cfg, target_service=service, domain=domain, public=public, private=private)

    deployer = Deployer(cfg)
    asyncio.run(deployer.deploy(tag=tag, build=False, push=False, target_service=service))


@compose_app.command("convert")
def compose_convert(
    compose_path: Path = typer.Option("docker-compose.yml", "--compose", "-f", help="Path to docker-compose.yml"),
    host: str = typer.Option("192.168.1.10", "--host", "-h", help="Target server IP for deploy.yml"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write to file instead of stdout (e.g. config/deploy.yml)"),
) -> None:
    """Convert an existing docker-compose.yml into an Aayi config/deploy.yml."""
    yaml_text = convert_compose_to_deploy_yaml(compose_path, web_host=host)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(yaml_text, encoding="utf-8")
        console.print(f"[green]✓ Successfully generated {output}[/green]")
    else:
        console.print(yaml_text)


@app.command()
def rollback(
    config_path: Path = typer.Option("config/deploy.yml", "--config", "-c", help="Path to deploy.yml"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Specific host to rollback (defaults to all web hosts)"),
) -> None:
    """Roll back Caddy traffic upstream to the previous healthy container."""
    cfg = load_config(config_path)
    manager = RollbackManager(cfg)
    asyncio.run(manager.rollback(target_host=host))


@app.command()
def logs(
    config_path: Path = typer.Option("config/deploy.yml", "--config", "-c", help="Path to deploy.yml"),
    role: str = typer.Option("web", "--role", "-r", help="Target server role ('web', 'workers', 'all')"),
    tail: int = typer.Option(100, "--tail", "-n", help="Number of recent log lines to display"),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Stream live logs in real time"),
) -> None:
    """Stream application logs concurrently across all target servers."""
    cfg = load_config(config_path)
    hosts = cfg.servers.hosts_for_role(role)
    if not hosts:
        console.print(f"[yellow]No hosts found for role '{role}'.[/yellow]")
        return

    runner = _build_runner(cfg)
    cmd = f"docker logs {'-f' if follow else ''} --tail {tail} $(docker ps -q --filter 'label=aayi.service={cfg.service}' | head -n 1)"

    async def _run() -> None:
        if follow:
            await runner.stream_logs(hosts, cmd)
        else:
            await runner.run_on_hosts(hosts, cmd)

    asyncio.run(_run())


@app.command()
def exec(
    command: str = typer.Argument(..., help="Command to execute inside the running service container"),
    config_path: Path = typer.Option("config/deploy.yml", "--config", "-c", help="Path to deploy.yml"),
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Specific host (defaults to first web host)"),
) -> None:
    """Execute a command inside the currently running application container."""
    cfg = load_config(config_path)
    target_host = host or (cfg.servers.web[0] if cfg.servers.web else None)
    if not target_host:
        console.print("[red]No target host available.[/red]")
        raise typer.Exit(1)

    runner = _build_runner(cfg)
    docker_cmd = f"docker exec -it $(docker ps -q --filter 'label=aayi.service={cfg.service}' | head -n 1) {command}"
    asyncio.run(runner.run_on_host(target_host, docker_cmd))


@proxy_app.command("status")
def proxy_status(
    config_path: Path = typer.Option("config/deploy.yml", "--config", "-c", help="Path to deploy.yml"),
) -> None:
    """View current Caddy proxy status and routing across web hosts."""
    cfg = load_config(config_path)
    runner = _build_runner(cfg)
    caddy = CaddyManager(runner, cfg.proxy)
    asyncio.run(caddy.get_status(cfg.servers.web))


@proxy_app.command("reload")
def proxy_reload(
    config_path: Path = typer.Option("config/deploy.yml", "--config", "-c", help="Path to deploy.yml"),
    port: int = typer.Option(..., "--port", "-p", help="Target upstream port to route traffic to"),
) -> None:
    """Force Caddy to route traffic to a specific upstream port."""
    cfg = load_config(config_path)
    runner = _build_runner(cfg)
    caddy = CaddyManager(runner, cfg.proxy)
    asyncio.run(caddy.update_upstream(cfg.servers.web, target_port=port))


@accessory_app.command("boot")
def accessory_boot(
    name: str = typer.Argument(..., help="Name of the accessory to boot (e.g. postgres, redis)"),
    config_path: Path = typer.Option("config/deploy.yml", "--config", "-c", help="Path to deploy.yml"),
) -> None:
    """Start an accessory container defined in deploy.yml."""
    cfg = load_config(config_path)
    manager = AccessoryManager(cfg)
    asyncio.run(manager.boot(name))


@accessory_app.command("stop")
def accessory_stop(
    name: str = typer.Argument(..., help="Name of the accessory to stop"),
    config_path: Path = typer.Option("config/deploy.yml", "--config", "-c", help="Path to deploy.yml"),
) -> None:
    """Stop an accessory container."""
    cfg = load_config(config_path)
    manager = AccessoryManager(cfg)
    asyncio.run(manager.stop(name))


@accessory_app.command("reboot")
def accessory_reboot(
    name: str = typer.Argument(..., help="Name of the accessory to reboot"),
    config_path: Path = typer.Option("config/deploy.yml", "--config", "-c", help="Path to deploy.yml"),
) -> None:
    """Restart an accessory container."""
    cfg = load_config(config_path)
    manager = AccessoryManager(cfg)
    asyncio.run(manager.reboot(name))


@accessory_app.command("logs")
def accessory_logs(
    name: str = typer.Argument(..., help="Name of the accessory to stream logs for"),
    config_path: Path = typer.Option("config/deploy.yml", "--config", "-c", help="Path to deploy.yml"),
    tail: int = typer.Option(100, "--tail", "-n", help="Number of lines to tail"),
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Stream logs in real-time"),
) -> None:
    """View or stream logs from an accessory container."""
    cfg = load_config(config_path)
    manager = AccessoryManager(cfg)
    asyncio.run(manager.logs(name, follow=follow, tail=tail))


@app.command()
def version() -> None:
    """Display Aayi version information."""
    console.print(f"[bold cyan]Aayi[/bold cyan] version [bold green]{__version__}[/bold green]")


if __name__ == "__main__":
    app()
