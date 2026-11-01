"""Caddy reverse proxy manager with dynamic zero-downtime multi-domain routing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from rich.console import Console

from aayi.config.models import ProxyConfig
from aayi.ssh.runner import SSHFleetRunner, SSHResult

console = Console()

CADDY_CONTAINER_NAME = "aayi-caddy"
CADDY_DOCKER_IMAGE = "caddy:2.8-alpine"


@dataclass
class ServiceRoute:
    """Upstream routing specification for a public service container."""
    service_name: str
    domains: List[str]
    target_port: int
    tls: bool = True


class CaddyManager:
    """Manages Caddy on remote hosts and updates routing via Caddy's dynamic admin API."""

    def __init__(self, runner: SSHFleetRunner, config: ProxyConfig) -> None:
        self.runner = runner
        self.config = config

    def generate_caddyfile(self, target_port: int, domains: Optional[List[str]] = None) -> str:
        """Generate a single-service Caddyfile pointing to the specified target port."""
        route = ServiceRoute(
            service_name="default",
            domains=domains or self.config.all_domains,
            target_port=target_port,
            tls=self.config.tls,
        )
        return self.generate_multi_service_caddyfile([route])

    def generate_multi_service_caddyfile(self, routes: List[ServiceRoute]) -> str:
        """Generate a Caddyfile containing routing blocks for all public services."""
        lines: List[str] = [
            "{\n  admin 127.0.0.1:2019\n"
        ]
        if self.config.email:
            lines.append(f"  email {self.config.email}\n")
        lines.append("}\n\n")

        valid_routes = [r for r in routes if r.domains]
        if not valid_routes:
            # Fallback if no public domains defined
            lines.append(":80 {\n  respond \"Aayi - No public services configured\" 200\n}\n")
            return "".join(lines)

        for route in valid_routes:
            domain_list = " ".join(route.domains)
            lines.append(f"# Service: {route.service_name}\n")
            lines.append(f"{domain_list} {{\n")
            if not route.tls:
                lines.append("  tls off\n")
            lines.append(f"  reverse_proxy 127.0.0.1:{route.target_port} {{\n")
            lines.append("    lb_try_duration 5s\n")
            lines.append("  }\n")
            lines.append("}\n\n")

        return "".join(lines)

    async def ensure_running(self, hosts: List[str]) -> List[SSHResult]:
        """Ensure Caddy is running in a host-networked Docker container on each host."""
        console.print("[bold blue]Checking Caddy reverse proxy on hosts...[/bold blue]")

        check_cmd = f"docker inspect -f '{{{{.State.Running}}}}' {CADDY_CONTAINER_NAME} 2>/dev/null || echo 'false'"
        results = await self.runner.run_on_hosts(hosts, check_cmd, echo=False)

        hosts_to_start: List[str] = []
        for res in results:
            if "true" not in res.stdout:
                hosts_to_start.append(res.host)

        if not hosts_to_start:
            console.print("[green]✓ Caddy is running on all web hosts.[/green]")
            return results

        console.print(f"[yellow]Starting Caddy on {len(hosts_to_start)} host(s)...[/yellow]")
        start_cmd = (
            f"docker rm -f {CADDY_CONTAINER_NAME} 2>/dev/null || true; "
            f"docker run -d "
            f"--name {CADDY_CONTAINER_NAME} "
            f"--restart unless-stopped "
            f"--network host "
            f"-v aayi_caddy_data:/data "
            f"-v aayi_caddy_config:/config "
            f"{CADDY_DOCKER_IMAGE}"
        )
        return await self.runner.run_on_hosts(hosts_to_start, start_cmd)

    async def update_upstream(self, hosts: List[str], target_port: int) -> List[SSHResult]:
        """Dynamically load new routing into Caddy's dynamic Admin API without restarting."""
        caddyfile_content = self.generate_caddyfile(target_port)
        return await self._apply_caddyfile(hosts, caddyfile_content, summary=f"port {target_port}")

    async def update_multi_service_routes(self, hosts: List[str], routes: List[ServiceRoute]) -> List[SSHResult]:
        """Dynamically update Caddy with routes for multiple public services and their respective domains."""
        caddyfile_content = self.generate_multi_service_caddyfile(routes)
        summary = ", ".join([f"{r.service_name} -> {r.target_port}" for r in routes if r.domains]) or "no public routes"
        return await self._apply_caddyfile(hosts, caddyfile_content, summary=summary)

    async def _apply_caddyfile(self, hosts: List[str], caddyfile_content: str, summary: str) -> List[SSHResult]:
        """Post Caddyfile directly to Caddy's /load endpoint across hosts."""
        console.print(f"[bold cyan]Updating Caddy dynamic routing ({summary})...[/bold cyan]")
        update_cmd = (
            f"curl -s -f -X POST http://127.0.0.1:2019/load "
            f"-H 'Content-Type: text/caddyfile' "
            f"--data-binary @- << 'EOF'\n{caddyfile_content}\nEOF"
        )
        results = await self.runner.run_on_hosts(hosts, update_cmd, echo=False)
        for res in results:
            color = self.runner.get_host_color(res.host)
            if res.success:
                console.print(f"[{color}][{res.host}][/{color}] [green]✓ Caddy dynamic routes updated.[/green]")
            else:
                console.print(f"[{color}][{res.host}][/{color}] [red]Failed to update Caddy routes: {res.stderr}[/red]")
        return results

    async def get_status(self, hosts: List[str]) -> List[SSHResult]:
        """Fetch current Caddy configuration from hosts."""
        cmd = "curl -s http://127.0.0.1:2019/config/ | jq . 2>/dev/null || curl -s http://127.0.0.1:2019/config/"
        return await self.runner.run_on_hosts(hosts, cmd)
