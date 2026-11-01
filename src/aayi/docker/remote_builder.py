"""Remote builder using rsync delta-sync and native VPS Docker build."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional

from rich.console import Console

from aayi.ssh.runner import SSHFleetRunner

console = Console()


class RemoteSyncBuilder:
    """Syncs source code via rsync and executes Docker builds directly on remote servers."""

    def __init__(self, runner: SSHFleetRunner) -> None:
        self.runner = runner

    async def sync_and_build(
        self,
        host: str,
        service: str,
        image_tag: str,
        context: str = ".",
        dockerfile: str = "Dockerfile",
        user: Optional[str] = None,
        port: Optional[int] = None,
        key: Optional[str] = None,
    ) -> None:
        """Rsync source context to target host and run native Docker build."""
        u = user or self.runner.default_user
        p = port or self.runner.default_port
        k = key or self.runner.default_key
        color = self.runner.get_host_color(host)

        remote_dir = f"/var/aayi/builds/{service}"
        console.print(f"[{color}][{host}][/{color}] [bold cyan]Syncing source code with rsync to {remote_dir}...[/bold cyan]")

        # 1. Ensure remote build directory exists
        await self.runner.run_on_host(host, f"mkdir -p {remote_dir}", echo=False)

        # 2. Build rsync command
        ssh_cmd = f"ssh -p {p}"
        if k:
            ssh_cmd += f" -i {k}"
        if self.runner.known_hosts is None:
            ssh_cmd += " -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

        rsync_cmd = [
            "rsync", "-az", "--delete",
            "-e", ssh_cmd,
            "--exclude", ".git",
            "--exclude", ".venv",
            "--exclude", "__pycache__",
            "--exclude", "*.pyc",
        ]

        # Use .dockerignore if available
        dockerignore = Path(".dockerignore")
        if dockerignore.is_file():
            rsync_cmd.extend(["--exclude-from", str(dockerignore)])

        # Trailing slash ensures directory contents are synced into remote_dir
        src_path = str(Path(context).resolve()) + "/"
        dest = f"{u}@{host}:{remote_dir}/"
        rsync_cmd.extend([src_path, dest])

        proc = await asyncio.create_subprocess_exec(*rsync_cmd)
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"Rsync failed with exit code {proc.returncode}")

        console.print(f"[{color}][{host}][/{color}] [green]✓ Source synced via rsync.[/green]")

        # 3. Build image natively on remote host using host Docker daemon and cache
        console.print(f"[{color}][{host}][/{color}] [bold blue]Building Docker image natively on remote host: [cyan]{image_tag}[/cyan]...[/bold blue]")
        build_cmd = f"docker build -t {image_tag} -f {remote_dir}/{dockerfile} {remote_dir}"
        await self.runner.run_on_host(host, build_cmd, check=True)
        console.print(f"[{color}][{host}][/{color}] [green]✓ Remote Docker build completed successfully.[/green]")
