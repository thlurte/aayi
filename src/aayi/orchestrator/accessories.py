"""Manages attached stateful services (Postgres, Redis, MySQL, etc.)."""

from __future__ import annotations

import shlex
from typing import Optional

from rich.console import Console

from aayi.config.models import AccessoryConfig, DeployConfig
from aayi.ssh.runner import SSHFleetRunner

console = Console()


class AccessoryManager:
    """Orchestrates persistent accessories with volume mounts and host bindings."""

    def __init__(self, config: DeployConfig) -> None:
        self.config = config
        self.runner = SSHFleetRunner(
            default_user=config.servers.user,
            default_port=config.servers.ssh_port,
            default_key=config.servers.ssh_key,
        )

    def _get_accessory(self, name: str) -> AccessoryConfig:
        if name not in self.config.accessories:
            valid = ", ".join(self.config.accessories.keys()) or "none"
            raise ValueError(f"Accessory '{name}' not found in deploy.yml. Available: {valid}")
        return self.config.accessories[name]

    async def boot(self, name: str) -> None:
        """Start an accessory container with persistent volumes."""
        acc = self._get_accessory(name)
        container_name = f"{self.config.service}-{name}"
        color = self.runner.get_host_color(acc.host)

        console.print(f"[{color}][{acc.host}][/{color}] Booting accessory [cyan]{name}[/cyan] ({acc.image})...")

        # Ensure port binds to localhost if not specified otherwise, to avoid exposing database to public internet
        port_flag = ""
        if acc.port:
            port_val = str(acc.port)
            if ":" in port_val and not port_val.startswith("127.0.0.1"):
                port_val = f"127.0.0.1:{port_val}"
            port_flag = f"-p {port_val}"

        vol_flags = " ".join([f"-v {shlex.quote(v)}" for v in acc.volumes])
        env_flags = " ".join([f"-e {shlex.quote(f'{k}={v}')}" for k, v in acc.env.items()])

        cmd = (
            f"docker network inspect aayi-net >/dev/null 2>&1 || docker network create aayi-net; "
            f"docker rm -f {shlex.quote(container_name)} 2>/dev/null || true; "
            f"docker run -d "
            f"--name {shlex.quote(container_name)} "
            f"--network aayi-net "
            f"--restart unless-stopped "
            f"{port_flag} {vol_flags} {env_flags} "
            f"{shlex.quote(acc.image)}"
        )
        res = await self.runner.run_on_host(acc.host, cmd, check=True)
        if res.success:
            console.print(f"[{color}][{acc.host}][/{color}] [green]✓ Accessory {name} is running securely on aayi-net.[/green]")

    async def stop(self, name: str) -> None:
        """Stop an accessory container."""
        acc = self._get_accessory(name)
        container_name = f"{self.config.service}-{name}"
        console.print(f"Stopping accessory {name} on {acc.host}...")
        await self.runner.run_on_host(acc.host, f"docker stop {container_name} 2>/dev/null || true")
        console.print(f"[green]✓ Accessory {name} stopped.[/green]")

    async def reboot(self, name: str) -> None:
        """Reboot an accessory container."""
        acc = self._get_accessory(name)
        container_name = f"{self.config.service}-{name}"
        console.print(f"Restarting accessory {name} on {acc.host}...")
        await self.runner.run_on_host(acc.host, f"docker restart {container_name}")
        console.print(f"[green]✓ Accessory {name} restarted.[/green]")

    async def logs(self, name: str, follow: bool = True, tail: int = 100) -> None:
        """Stream logs for an accessory container."""
        acc = self._get_accessory(name)
        container_name = f"{self.config.service}-{name}"
        follow_flag = "-f" if follow else ""
        cmd = f"docker logs {follow_flag} --tail {tail} {container_name}"
        if follow:
            await self.runner.stream_logs([acc.host], cmd)
        else:
            await self.runner.run_on_host(acc.host, cmd)
