"""High-performance asynchronous SSH fleet execution engine powered by AsyncSSH."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import asyncssh
from rich.console import Console

console = Console()

# Rotating host colors for distinct prefixing in multi-server output
HOST_COLORS = ["cyan", "magenta", "green", "yellow", "blue", "bright_cyan", "bright_magenta"]


@dataclass
class SSHResult:
    """Execution output from a single remote host."""
    host: str
    command: str
    exit_status: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.exit_status == 0


class SSHFleetRunner:
    """Orchestrates parallel SSH command execution across server fleets."""

    def __init__(
        self,
        default_user: str = "root",
        default_port: int = 22,
        default_key: Optional[str] = None,
        default_keys: Optional[List[str]] = None,
        password: Optional[str] = None,
        passphrase: Optional[str] = None,
        known_hosts: Optional[str] = None,
    ) -> None:
        self.default_user = default_user
        self.default_port = default_port
        keys = list(default_keys or [])
        if default_key and default_key not in keys:
            keys.append(default_key)
        self.default_key = default_key or (keys[0] if keys else None)
        self.default_keys = keys or None
        self.password = password
        self.passphrase = passphrase
        self.known_hosts = known_hosts
        self._host_color_map: Dict[str, str] = {}

    def get_host_color(self, host: str) -> str:
        """Assign a consistent color for each host label."""
        if host not in self._host_color_map:
            idx = len(self._host_color_map) % len(HOST_COLORS)
            self._host_color_map[host] = HOST_COLORS[idx]
        return self._host_color_map[host]

    async def run_on_host(
        self,
        host: str,
        command: str,
        user: Optional[str] = None,
        port: Optional[int] = None,
        key: Optional[str] = None,
        check: bool = False,
        echo: bool = True,
    ) -> SSHResult:
        """Execute a single command on a remote host over SSH."""
        user = user or self.default_user
        port = port or self.default_port
        client_keys = [key] if key else self.default_keys
        color = self.get_host_color(host)

        if echo:
            console.print(f"[{color}][{host}][/{color}] [dim]$ {command}[/dim]")

        options = asyncssh.SSHClientConnectionOptions(
            known_hosts=self.known_hosts,
            client_keys=client_keys,
            password=self.password,
            passphrase=self.passphrase,
        )

        try:
            async with asyncssh.connect(host, port=port, username=user, options=options) as conn:
                result = await conn.run(command, check=False)
                stdout = result.stdout or ""
                stderr = result.stderr or ""
                exit_status = result.exit_status if result.exit_status is not None else 0

                if stdout and echo:
                    for line in stdout.strip().splitlines():
                        console.print(f"[{color}][{host}][/{color}] {line}")

                if stderr and echo:
                    for line in stderr.strip().splitlines():
                        console.print(f"[{color}][{host}][/{color}] [red]{line}[/red]")

                res = SSHResult(
                    host=host,
                    command=command,
                    exit_status=exit_status,
                    stdout=stdout,
                    stderr=stderr,
                )

                if check and not res.success:
                    raise RuntimeError(f"Command failed on {host} with code {exit_status}: {stderr.strip() or stdout.strip()}")

                return res

        except (asyncssh.Error, OSError) as exc:
            err_msg = f"SSH error connecting to {host}: {exc}"
            console.print(f"[{color}][{host}][/{color}] [bold red]{err_msg}[/bold red]")
            res = SSHResult(host=host, command=command, exit_status=-1, stdout="", stderr=err_msg)
            if check:
                raise RuntimeError(err_msg) from exc
            return res

    async def run_on_hosts(
        self,
        hosts: Sequence[str],
        command: str,
        user: Optional[str] = None,
        port: Optional[int] = None,
        key: Optional[str] = None,
        check: bool = False,
        echo: bool = True,
    ) -> List[SSHResult]:
        """Execute a command concurrently across all specified hosts."""
        if not hosts:
            return []

        tasks = [
            self.run_on_host(host, command, user=user, port=port, key=key, check=check, echo=echo)
            for host in hosts
        ]
        return await asyncio.gather(*tasks)

    async def stream_logs(
        self,
        hosts: Sequence[str],
        command: str,
        user: Optional[str] = None,
        port: Optional[int] = None,
        key: Optional[str] = None,
    ) -> None:
        """Stream real-time log lines from multiple hosts concurrently."""
        async def _stream_single(host: str) -> None:
            user_name = user or self.default_user
            p = port or self.default_port
            client_keys = [key] if key else self.default_keys
            color = self.get_host_color(host)
            options = asyncssh.SSHClientConnectionOptions(
                known_hosts=self.known_hosts,
                client_keys=client_keys,
                password=self.password,
                passphrase=self.passphrase,
            )
            try:
                async with asyncssh.connect(host, port=p, username=user_name, options=options) as conn:
                    async with conn.create_process(command) as proc:
                        async for line in proc.stdout:
                            console.print(f"[{color}][{host}][/{color}] {line.rstrip()}")
                        async for line in proc.stderr:
                            console.print(f"[{color}][{host}][/{color}] [red]{line.rstrip()}[/red]")
            except (asyncssh.Error, OSError) as exc:
                console.print(f"[{color}][{host}][/{color}] [bold red]Stream disconnected: {exc}[/bold red]")

        await asyncio.gather(*[_stream_single(h) for h in hosts])
