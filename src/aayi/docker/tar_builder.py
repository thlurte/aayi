"""In-memory zero-dependency tar streaming builder for native remote Docker builds."""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path
from typing import Callable, List, Optional, Set

import asyncssh
from rich.console import Console

from aayi.ssh.runner import SSHFleetRunner

console = Console()

DEFAULT_EXCLUDES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".env",
    "dist",
    "build",
    ".DS_Store",
}


def _load_ignore_patterns(context_dir: Path) -> Set[str]:
    """Load exclusion patterns from .dockerignore or .gitignore."""
    patterns = set(DEFAULT_EXCLUDES)
    for ignore_file in [context_dir / ".dockerignore", context_dir / ".gitignore"]:
        if ignore_file.is_file():
            for line in ignore_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.add(line.rstrip("/"))
    return patterns


def create_in_memory_archive(context_path: Path | str = ".") -> bytes:
    """Create a compressed .tar.gz archive in memory, excluding ignored files and directories."""
    context = Path(context_path).resolve()
    ignore_patterns = _load_ignore_patterns(context)

    buffer = io.BytesIO()

    def filter_func(tarinfo: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
        name_parts = Path(tarinfo.name).parts
        for part in name_parts:
            if part in ignore_patterns or part.startswith(".git"):
                return None
            for pattern in ignore_patterns:
                if pattern.startswith("*") and part.endswith(pattern[1:]):
                    return None
        return tarinfo

    with tarfile.open(fileobj=buffer, mode="w:gz", dereference=True) as tar:
        for item in context.iterdir():
            if item.name in ignore_patterns:
                continue
            tar.add(item, arcname=item.name, filter=filter_func)

    buffer.seek(0)
    return buffer.getvalue()


class RemoteTarBuilder:
    """Streams project source directly over SSH in-memory and builds natively on remote Docker."""

    def __init__(self, runner: SSHFleetRunner) -> None:
        self.runner = runner

    async def stream_and_build(
        self,
        host: str,
        service: str,
        image_tag: str,
        context: str = ".",
        dockerfile: str = "Dockerfile",
    ) -> None:
        """Bundle source in memory, stream over SSH, and run Docker build natively on the VPS."""
        color = self.runner.get_host_color(host)
        remote_dir = f"/var/aayi/builds/{service}"

        console.print(f"[{color}][{host}][/{color}] [bold cyan]Creating in-memory compressed archive...[/bold cyan]")
        archive_bytes = create_in_memory_archive(context)
        size_mb = len(archive_bytes) / (1024 * 1024)
        console.print(f"[{color}][{host}][/{color}] Compressed size: [green]{size_mb:.2f} MB[/green]. Streaming over SSH...")

        # 1. Stream archive to remote host and extract
        options = asyncssh.SSHClientConnectionOptions(
            known_hosts=self.runner.known_hosts,
            client_keys=[self.runner.default_key] if self.runner.default_key else None,
        )

        async with asyncssh.connect(
            host,
            port=self.runner.default_port,
            username=self.runner.default_user,
            options=options,
        ) as conn:
            extract_cmd = f"rm -rf {remote_dir} && mkdir -p {remote_dir} && tar -xzf - -C {remote_dir}"
            async with conn.create_process(extract_cmd) as proc:
                proc.stdin.write(archive_bytes)
                await proc.stdin.drain()
                proc.stdin.close()
                await proc.wait()
                if proc.exit_status != 0:
                    stderr = await proc.stderr.read()
                    raise RuntimeError(f"Failed to extract archive on {host}: {stderr}")

        console.print(f"[{color}][{host}][/{color}] [green]✓ Source extracted on remote host.[/green]")

        # 2. Build image natively using remote host's Docker daemon & layer cache
        console.print(f"[{color}][{host}][/{color}] [bold blue]Building image natively on VPS: [cyan]{image_tag}[/cyan]...[/bold blue]")
        build_cmd = f"docker build -t {image_tag} -f {remote_dir}/{dockerfile} {remote_dir}"
        await self.runner.run_on_host(host, build_cmd, check=True)
        console.print(f"[{color}][{host}][/{color}] [green]✓ Native remote build complete.[/green]")
