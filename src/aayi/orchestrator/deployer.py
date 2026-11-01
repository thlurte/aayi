"""Full zero-downtime deployment orchestration pipeline."""

from __future__ import annotations

import asyncio
import os
from typing import Dict, List, Optional

from rich.console import Console

from aayi.config.models import DeployConfig
from aayi.docker.builder import DockerBuilder, get_git_revision
from aayi.docker.remote import RemoteDocker
from aayi.proxy.caddy import CaddyManager
from aayi.ssh.runner import SSHFleetRunner

console = Console()


class Deployer:
    """Coordinates the entire zero-downtime deployment lifecycle."""

    def __init__(self, config: DeployConfig) -> None:
        self.config = config
        self.runner = SSHFleetRunner(
            default_user=config.servers.user,
            default_port=config.servers.ssh_port,
            default_key=config.servers.ssh_key,
        )
        self.remote_docker = RemoteDocker(self.runner)
        self.caddy = CaddyManager(self.runner, config.proxy)
        self.builder = DockerBuilder()

    def _resolve_env_vars(self) -> Dict[str, str]:
        """Combine clear text env vars with resolved secrets from local environment."""
        env: Dict[str, str] = dict(self.config.env.clear)
        for secret_name in self.config.env.secret:
            val = os.environ.get(secret_name)
            if val is not None:
                env[secret_name] = val
            else:
                console.print(f"[yellow]Warning: Secret variable '{secret_name}' is not set in local environment.[/yellow]")
        return env

    async def deploy(
        self,
        tag: Optional[str] = None,
        build: bool = True,
        push: bool = True,
    ) -> None:
        """Execute the end-to-end zero-downtime deployment."""
        revision = tag or get_git_revision()
        full_image_tag = f"{self.config.image}:{revision}"
        web_hosts = self.config.servers.web
        worker_hosts = self.config.servers.workers
        all_hosts = self.config.servers.all_hosts

        console.rule(f"[bold green]Starting Deployment: {self.config.service} ({revision})[/bold green]")

        # Step 1: Build & push locally if requested
        if build:
            self.builder.build_and_push(
                image_base=self.config.image,
                tag=revision,
                push=push,
            )

        # Step 2: Ensure Docker is ready on all remote hosts
        await self.remote_docker.ensure_docker(all_hosts)

        # Step 3: Login to registry on target servers
        await self.remote_docker.login_registry(all_hosts, self.config.registry)

        # Step 4: Pull image on all target hosts in parallel
        await self.remote_docker.pull_image(all_hosts, full_image_tag)

        # Step 5: Ensure Caddy is running on web hosts
        if web_hosts:
            await self.caddy.ensure_running(web_hosts)

        env_vars = self._resolve_env_vars()

        # Step 6: Deploy web instances with zero-downtime rolling container swap
        if web_hosts:
            console.rule("[bold cyan]Deploying Web Instances[/bold cyan]")
            for host in web_hosts:
                await self._deploy_web_host(host, revision, full_image_tag, env_vars)

        # Step 7: Deploy worker instances (if any)
        if worker_hosts:
            console.rule("[bold cyan]Deploying Worker Instances[/bold cyan]")
            for host in worker_hosts:
                await self._deploy_worker_host(host, revision, full_image_tag, env_vars)

        # Step 8: Clean up stale images
        await self.remote_docker.prune(all_hosts)

        console.rule(f"[bold green]✓ Deployment completed successfully: {self.config.service}[/bold green]")

    async def _deploy_web_host(
        self,
        host: str,
        revision: str,
        image_tag: str,
        env_vars: Dict[str, str],
    ) -> None:
        """Roll out new web container on a single host and swap Caddy traffic."""
        color = self.runner.get_host_color(host)
        console.print(f"[{color}][{host}][/{color}] Preparing zero-downtime swap...")

        # 1. Identify currently running containers for this service
        existing_containers = await self.remote_docker.get_service_containers(host, self.config.service)
        old_container_names = [c.get("Names", "") for c in existing_containers if c.get("Names")]

        # 2. Pick an unused port for the new container
        new_port = await self.remote_docker.find_available_port(host, base_port=self.config.proxy.app_port)
        new_container_name = f"{self.config.service}-web-{revision}-{new_port}"

        console.print(f"[{color}][{host}][/{color}] Booting new container [cyan]{new_container_name}[/cyan] on internal port {new_port}...")

        labels = {
            "aayi.service": self.config.service,
            "aayi.role": "web",
            "aayi.revision": revision,
            "aayi.port": str(new_port),
        }

        # 3. Boot new container
        await self.remote_docker.boot_container(
            host=host,
            name=new_container_name,
            image_tag=image_tag,
            host_port=new_port,
            container_port=self.config.proxy.app_port,
            env_vars=env_vars,
            labels=labels,
        )

        # 4. Perform health check
        healthy = await self.remote_docker.wait_for_healthy(
            host=host,
            port=new_port,
            path=self.config.proxy.healthcheck_path,
            timeout=self.config.proxy.healthcheck_timeout,
            interval=self.config.proxy.healthcheck_interval,
        )

        if not healthy:
            console.print(f"[{color}][{host}][/{color}] [bold red]Health check failed! Rolling back container {new_container_name}...[/bold red]")
            await self.remote_docker.stop_container(host, new_container_name)
            raise RuntimeError(f"Deployment aborted on {host}: new container failed healthcheck.")

        # 5. Dynamically switch Caddy traffic to new container port
        await self.caddy.update_upstream([host], new_port)

        # 6. Drain period for in-flight requests
        drain = self.config.proxy.drain_timeout
        console.print(f"[{color}][{host}][/{color}] Draining in-flight requests for {drain}s...")
        await asyncio.sleep(drain)

        # 7. Stop old containers
        for old_name in old_container_names:
            if old_name != new_container_name:
                console.print(f"[{color}][{host}][/{color}] Stopping retired container [dim]{old_name}[/dim]...")
                await self.remote_docker.stop_container(host, old_name)

        console.print(f"[{color}][{host}][/{color}] [green]✓ Web rollout complete on {host}[/green]")

    async def _deploy_worker_host(
        self,
        host: str,
        revision: str,
        image_tag: str,
        env_vars: Dict[str, str],
    ) -> None:
        """Roll out new worker container on a host."""
        color = self.runner.get_host_color(host)
        worker_container_name = f"{self.config.service}-worker-{revision}"

        # Find and stop old worker
        cmd = f"docker ps -q --filter 'name={self.config.service}-worker' | xargs -r docker stop"
        await self.runner.run_on_host(host, cmd, echo=False)

        labels = {
            "aayi.service": self.config.service,
            "aayi.role": "worker",
            "aayi.revision": revision,
        }

        console.print(f"[{color}][{host}][/{color}] Booting worker container [cyan]{worker_container_name}[/cyan]...")
        env_flags = " ".join([f"-e {k}='{v}'" for k, v in env_vars.items()])
        label_flags = " ".join([f"--label {k}='{v}'" for k, v in labels.items()])
        run_cmd = f"docker run -d --name {worker_container_name} --restart unless-stopped {env_flags} {label_flags} {image_tag}"
        await self.runner.run_on_host(host, run_cmd, check=True)
        console.print(f"[{color}][{host}][/{color}] [green]✓ Worker rollout complete on {host}[/green]")
