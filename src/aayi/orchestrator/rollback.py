"""Rollback orchestration to instantly revert Caddy routing to previous container."""

from __future__ import annotations

import re
from typing import Optional

from rich.console import Console

from aayi.config.models import DeployConfig
from aayi.proxy.caddy import CaddyManager
from aayi.ssh.runner import SSHFleetRunner

console = Console()


class RollbackManager:
    """Manages instant rollbacks by reverting Caddy upstream to previous healthy containers."""

    def __init__(self, config: DeployConfig) -> None:
        self.config = config
        self.runner = SSHFleetRunner(
            default_user=config.servers.user,
            default_port=config.servers.ssh_port,
            default_key=config.servers.ssh_key,
            default_keys=config.servers.ssh_keys,
            password=config.servers.ssh_password,
            passphrase=config.servers.passphrase,
        )
        self.caddy = CaddyManager(self.runner, config.proxy)

    async def rollback(self, target_host: Optional[str] = None) -> None:
        """Find previous running container for this service and switch Caddy traffic back to it."""
        hosts = [target_host] if target_host else self.config.servers.web
        if not hosts:
            console.print("[yellow]No web hosts configured for rollback.[/yellow]")
            return

        console.rule(f"[bold yellow]Rolling back: {self.config.service}[/bold yellow]")

        for host in hosts:
            color = self.runner.get_host_color(host)
            console.print(f"[{color}][{host}][/{color}] Discovering candidate containers for rollback...")

            # Get running containers matching service name
            cmd = f"docker ps --filter 'name={self.config.service}-web' --format '{{{{.Names}}}} {{{{.Ports}}}}'"
            res = await self.runner.run_on_host(host, cmd, echo=False)

            lines = [line.strip() for line in res.stdout.strip().splitlines() if line.strip()]
            if len(lines) < 2:
                console.print(f"[{color}][{host}][/{color}] [bold red]Cannot rollback: Fewer than 2 web containers found on {host}.[/bold red]")
                continue

            # The current container is typically the latest one (first in docker ps).
            # The rollback target is the second one in the list.
            target_line = lines[1]
            match = re.search(r"127\.0\.0\.1:(\d+)->", target_line)
            if not match:
                console.print(f"[{color}][{host}][/{color}] [bold red]Could not determine port from container: {target_line}[/bold red]")
                continue

            target_port = int(match.group(1))
            console.print(f"[{color}][{host}][/{color}] Reverting Caddy to previous container on port [cyan]{target_port}[/cyan]...")

            await self.caddy.update_upstream([host], target_port)
            console.print(f"[{color}][{host}][/{color}] [bold green]✓ Successfully rolled back to port {target_port}[/bold green]")
