"""Local Docker image builder using buildx."""

from __future__ import annotations

import subprocess
from typing import List, Optional

from rich.console import Console

console = Console()


def get_git_revision() -> str:
    """Return the current short Git commit hash, or 'latest' if not in a git repo."""
    try:
        output = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL)
        return output.decode("utf-8").strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "latest"


class DockerBuilder:
    """Builds and pushes multi-architecture images locally using Docker buildx."""

    def __init__(self, default_platforms: Optional[List[str]] = None) -> None:
        self.default_platforms = default_platforms or ["linux/amd64"]

    def build_and_push(
        self,
        image_base: str,
        tag: Optional[str] = None,
        dockerfile: str = "Dockerfile",
        context: str = ".",
        platforms: Optional[List[str]] = None,
        push: bool = True,
    ) -> str:
        """Build and optionally push the Docker image to the configured registry."""
        resolved_tag = tag or get_git_revision()
        full_image_tag = f"{image_base}:{resolved_tag}"
        target_platforms = ",".join(platforms or self.default_platforms)

        console.print(f"[bold blue]Building Docker image [cyan]{full_image_tag}[/cyan] (platforms: {target_platforms})...[/bold blue]")

        cmd = [
            "docker", "buildx", "build",
            "--platform", target_platforms,
            "-t", full_image_tag,
            "-f", dockerfile,
        ]
        if push:
            cmd.append("--push")
        else:
            cmd.append("--load")
        cmd.append(context)

        console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
        try:
            subprocess.run(cmd, check=True)
            console.print(f"[green]✓ Successfully built and pushed {full_image_tag}[/green]")
            return full_image_tag
        except subprocess.CalledProcessError as exc:
            console.print(f"[bold red]Docker build failed with exit code {exc.returncode}[/bold red]")
            raise
