"""Remote Docker management over SSH."""

from __future__ import annotations

import asyncio
import json
import shlex
import time
from typing import Dict, List, Optional

from rich.console import Console

from aayi.config.models import RegistryConfig
from aayi.ssh.runner import SSHFleetRunner, SSHResult

console = Console()


class RemoteDocker:
    """Manages Docker commands remotely on hosts via SSHFleetRunner."""

    def __init__(self, runner: SSHFleetRunner) -> None:
        self.runner = runner

    async def ensure_docker(self, hosts: List[str]) -> None:
        """Verify Docker is installed on all target hosts, or install it if missing."""
        console.print("[bold blue]Checking Docker installation on target hosts...[/bold blue]")
        results = await self.runner.run_on_hosts(hosts, "docker --version 2>&1", echo=False)

        missing_hosts: List[str] = []
        for res in results:
            if not res.success or "Docker version" not in res.stdout:
                missing_hosts.append(res.host)

        if missing_hosts:
            console.print(f"[yellow]Docker not detected on {len(missing_hosts)} host(s). Installing official Docker CE...[/yellow]")
            install_cmd = "curl -fsSL https://get.docker.com | sh && systemctl enable --now docker"
            install_res = await self.runner.run_on_hosts(missing_hosts, install_cmd, check=True)
            for r in install_res:
                console.print(f"[{self.runner.get_host_color(r.host)}][{r.host}][/{self.runner.get_host_color(r.host)}] [green]✓ Docker installed successfully.[/green]")
        else:
            console.print("[green]✓ Docker is installed and running on all hosts.[/green]")

    async def login_registry(self, hosts: List[str], registry: Optional[RegistryConfig]) -> None:
        """Authenticate with the Docker container registry if credentials are provided."""
        if not registry or not registry.username or not registry.password:
            return

        console.print(f"[bold blue]Authenticating with registry {registry.server}...[/bold blue]")
        cmd = (
            f"printf '%s' {shlex.quote(registry.password)} | "
            f"docker login {shlex.quote(registry.server)} -u {shlex.quote(registry.username)} --password-stdin"
        )
        # We run with echo=False to avoid leaking passwords in terminal
        results = await self.runner.run_on_hosts(hosts, cmd, echo=False)
        for r in results:
            if not r.success:
                raise RuntimeError(f"Registry login failed on {r.host}: {r.stderr}")
            console.print(f"[{self.runner.get_host_color(r.host)}][{r.host}][/{self.runner.get_host_color(r.host)}] [green]✓ Logged in to {registry.server}[/green]")

    async def pull_image(self, hosts: List[str], image_tag: str) -> None:
        """Pull the application image on all hosts concurrently."""
        console.print(f"[bold blue]Pulling image [cyan]{image_tag}[/cyan] on all hosts...[/bold blue]")
        cmd = f"docker pull {image_tag}"
        results = await self.runner.run_on_hosts(hosts, cmd, check=True)
        for r in results:
            console.print(f"[{self.runner.get_host_color(r.host)}][{r.host}][/{self.runner.get_host_color(r.host)}] [green]✓ Image pulled.[/green]")

    async def find_available_port(self, host: str, base_port: int = 8000) -> int:
        """Find an unused TCP port on the remote host starting from base_port."""
        # Use python to probe for an unbound port on localhost
        probe_cmd = (
            f"python3 -c \""
            f"import socket; "
            f"def is_free(p):\\n"
            f"  s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\\n"
            f"  try:\\n"
            f"    s.bind(('127.0.0.1', p)); s.close(); return True\\n"
            f"  except:\\n"
            f"    return False\\n"
            f"for port in range({base_port}, {base_port + 100}):\\n"
            f"  if is_free(port):\\n"
            f"    print(port); break\\n"
            f"\" 2>/dev/null || ss -tulpn | grep -q :{base_port} && echo {base_port + 1} || echo {base_port}"
        )
        res = await self.runner.run_on_host(host, probe_cmd, echo=False)
        output = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else str(base_port)
        try:
            return int(output)
        except ValueError:
            return base_port

    async def ensure_network(self, hosts: List[str], network: str = "aayi-net") -> None:
        """Ensure an internal Docker bridge network exists on all hosts."""
        cmd = f"docker network inspect {network} >/dev/null 2>&1 || docker network create {network}"
        await self.runner.run_on_hosts(hosts, cmd, echo=False)

    async def boot_container(
        self,
        host: str,
        name: str,
        image_tag: str,
        host_port: Optional[int],
        container_port: int,
        env_vars: Dict[str, str],
        labels: Dict[str, str],
        command: Optional[str] = None,
        network: str = "aayi-net",
    ) -> SSHResult:
        """Start an application container. If host_port is None, container is private with no host port binding."""
        env_flags = " ".join([f"-e {shlex.quote(f'{k}={v}')}" for k, v in env_vars.items()])
        label_flags = " ".join([f"--label {shlex.quote(f'{k}={v}')}" for k, v in labels.items()])
        cmd_suffix = f" {shlex.quote(command)}" if command else ""
        port_flag = f"-p 127.0.0.1:{host_port}:{container_port}" if host_port else ""

        run_cmd = (
            f"docker run -d "
            f"--name {shlex.quote(name)} "
            f"--network {shlex.quote(network)} "
            f"--restart unless-stopped "
            f"{port_flag} "
            f"{env_flags} "
            f"{label_flags} "
            f"{shlex.quote(image_tag)}{cmd_suffix}"
        )
        return await self.runner.run_on_host(host, run_cmd, check=True)

    async def wait_for_healthy(
        self,
        host: str,
        port: int,
        path: str = "/",
        timeout: int = 30,
        interval: int = 2,
    ) -> bool:
        """Poll the container's health endpoint until a 2xx or 3xx HTTP status is returned."""
        color = self.runner.get_host_color(host)
        console.print(f"[{color}][{host}][/{color}] Waiting for healthcheck at [cyan]http://127.0.0.1:{port}{path}[/cyan] (timeout {timeout}s)...")

        curl_cmd = f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 3 {shlex.quote(f'http://127.0.0.1:{port}{path}')} || true"
        deadline = time.time() + timeout

        while time.time() < deadline:
            res = await self.runner.run_on_host(host, curl_cmd, echo=False)
            status_code = res.stdout.strip()
            if status_code.startswith(("2", "3")):
                console.print(f"[{color}][{host}][/{color}] [green]✓ Health check passed (HTTP {status_code})[/green]")
                return True
            await asyncio.sleep(interval)

        console.print(f"[{color}][{host}][/{color}] [bold red]✗ Health check timed out after {timeout}s[/bold red]")
        return False

    async def get_service_containers(self, host: str, service: str) -> List[Dict[str, str]]:
        """List running containers belonging to a specific service."""
        cmd = f"docker ps --filter 'label=aayi.service={service}' --format '{{{{json .}}}}'"
        res = await self.runner.run_on_host(host, cmd, echo=False)
        containers: List[Dict[str, str]] = []
        for line in res.stdout.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    containers.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return containers

    async def stop_container(self, host: str, name: str, timeout: int = 10) -> SSHResult:
        """Stop and remove a container."""
        cmd = f"docker stop -t {timeout} {name} 2>/dev/null && docker rm -f {name} 2>/dev/null || true"
        return await self.runner.run_on_host(host, cmd, echo=False)

    async def prune(self, hosts: List[str]) -> None:
        """Prune dangling Docker images and stopped containers."""
        console.print("[dim]Pruning dangling images and stopped containers...[/dim]")
        cmd = "docker container prune -f && docker image prune -f"
        await self.runner.run_on_hosts(hosts, cmd, echo=False)
