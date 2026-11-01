"""Caddy reverse proxy manager with dynamic zero-downtime upstream routing."""

from __future__ import annotations

from typing import List, Optional

from rich.console import Console

from aayi.config.models import ProxyConfig
from aayi.ssh.runner import SSHFleetRunner, SSHResult

console = Console()

CADDY_CONTAINER_NAME = "aayi-caddy"
CADDY_DOCKER_IMAGE = "caddy:2.8-alpine"


class CaddyManager:
    """Manages Caddy on remote hosts and updates routing via Caddy's dynamic admin API."""

    def __init__(self, runner: SSHFleetRunner, config: ProxyConfig) -> None:
        self.runner = runner
        self.config = config

    def generate_caddyfile(self, target_port: int, domains: Optional[List[str]] = None) -> str:
        """Generate a Caddyfile pointing to the specified target port."""
        domains = domains or self.config.all_domains
        if not domains:
            domains = [":80"]

        lines: List[str] = [
            "{\n  admin 127.0.0.1:2019\n"
        ]
        if self.config.email:
            lines.append(f"  email {self.config.email}\n")
        lines.append("}\n\n")

        domain_list = " ".join(domains)
        lines.append(f"{domain_list} {{\n")
        if not self.config.tls:
            lines.append("  tls off\n")
        lines.append(f"  reverse_proxy 127.0.0.1:{target_port} {{\n")
        lines.append("    lb_try_duration 5s\n")
        lines.append("  }\n")
        lines.append("}\n")

        return "".join(lines)

    async def ensure_running(self, hosts: List[str]) -> List[SSHResult]:
        """Ensure Caddy is running in a host-networked Docker container on each host."""
        console.print("[bold blue]Checking Caddy reverse proxy on hosts...[/bold blue]")

        # Check if container exists and is running
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
        console.print(f"[bold cyan]Switching Caddy traffic to upstream port {target_port} across hosts...[/bold cyan]")

        # Escape single quotes for bash Heredoc
        update_cmd = (
            f"curl -s -f -X POST http://127.0.0.1:2019/load "
            f"-H 'Content-Type: text/caddyfile' "
            f"--data-binary @- << 'EOF'\n{caddyfile_content}\nEOF"
        )
        results = await self.runner.run_on_hosts(hosts, update_cmd, echo=False)
        for res in results:
            if res.success:
                console.print(f"[{self.runner.get_host_color(res.host)}][{res.host}][/{self.runner.get_host_color(res.host)}] [green]✓ Traffic switched to port {target_port}[/green]")
            else:
                console.print(f"[{self.runner.get_host_color(res.host)}][{res.host}][/{self.runner.get_host_color(res.host)}] [red]Failed to switch Caddy upstream: {res.stderr}[/red]")
        return results

    async def get_status(self, hosts: List[str]) -> List[SSHResult]:
        """Fetch current Caddy configuration from hosts."""
        cmd = "curl -s http://127.0.0.1:2019/config/ | jq . 2>/dev/null || curl -s http://127.0.0.1:2019/config/"
        return await self.runner.run_on_hosts(hosts, cmd)
