"""Full zero-downtime multi-container deployment orchestration pipeline."""

from __future__ import annotations

import asyncio
import os
from typing import Dict, List, Optional

from rich.console import Console

from aayi.config.models import DeployConfig, ServiceDefinition
from aayi.docker.builder import DockerBuilder, get_git_revision
from aayi.docker.remote import RemoteDocker
from aayi.proxy.caddy import CaddyManager, ServiceRoute
from aayi.ssh.runner import SSHFleetRunner

console = Console()


def _sort_services_by_dependencies(services: Dict[str, ServiceDefinition]) -> List[ServiceDefinition]:
    """Sort services topologically based on depends_on."""
    visited = set()
    result: List[ServiceDefinition] = []

    def visit(name: str):
        if name in visited or name not in services:
            return
        visited.add(name)
        s = services[name]
        for dep in s.depends_on:
            visit(dep)
        result.append(s)

    for s_name in services:
        visit(s_name)

    return result


class Deployer:
    """Coordinates the entire zero-downtime multi-container deployment lifecycle."""

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

    def _resolve_env_vars(self, service_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Combine clear text env vars with resolved secrets from local environment."""
        env: Dict[str, str] = dict(self.config.env.clear)
        if service_env:
            env.update(service_env)

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
        target_service: Optional[str] = None,
    ) -> None:
        """Execute the end-to-end multi-container zero-downtime deployment."""
        revision = tag or get_git_revision()
        all_hosts = self.config.servers.all_hosts
        web_hosts = self.config.servers.web

        # Filter services if a single target was specified
        services_map = self.config.services
        if target_service:
            if target_service not in services_map:
                raise ValueError(f"Service '{target_service}' not found. Available: {', '.join(services_map.keys())}")
            services_map = {target_service: services_map[target_service]}

        ordered_services = _sort_services_by_dependencies(services_map)

        console.rule(f"[bold green]Starting Deployment: {self.config.service or 'Aayi App'} ({revision})[/bold green]")
        console.print(f"[dim]Deploying services: {', '.join([s.name for s in ordered_services])}[/dim]")

        # Step 1: Ensure Docker and shared internal network on all hosts
        await self.remote_docker.ensure_docker(all_hosts)
        await self.remote_docker.ensure_network(all_hosts, network="aayi-net")

        # Step 2: Login to registry on target servers
        await self.remote_docker.login_registry(all_hosts, self.config.registry)

        # Step 3: Build & push locally if requested
        for s_def in ordered_services:
            image_base = s_def.image or self.config.image
            if not image_base:
                continue
            if build and (s_def.build_context or s_def.dockerfile):
                self.builder.build_and_push(
                    image_base=image_base,
                    tag=revision,
                    dockerfile=s_def.dockerfile or "Dockerfile",
                    context=s_def.build_context or ".",
                    push=push,
                )

        # Step 4: Pull images on target hosts
        for s_def in ordered_services:
            image_base = s_def.image or self.config.image
            if image_base:
                full_image_tag = f"{image_base}:{revision}"
                await self.remote_docker.pull_image(all_hosts, full_image_tag)

        # Step 5: Ensure Caddy is running on web hosts if any public service exists
        has_public = any(s.public and s.all_domains for s in ordered_services)
        if has_public and web_hosts:
            await self.caddy.ensure_running(web_hosts)

        # Step 6: Deploy services in topological dependency order
        active_routes: List[ServiceRoute] = []

        for s_def in ordered_services:
            image_base = s_def.image or self.config.image
            if not image_base:
                continue
            full_image_tag = f"{image_base}:{revision}"
            env_vars = self._resolve_env_vars(s_def.env.clear)

            if s_def.public and s_def.all_domains:
                # Public Service with dedicated domain(s)
                console.rule(f"[bold cyan]Deploying Public Service: {s_def.name} ({', '.join(s_def.all_domains)})[/bold cyan]")
                for host in web_hosts:
                    port = await self._deploy_public_service(host, s_def, revision, full_image_tag, env_vars)
                    active_routes.append(
                        ServiceRoute(
                            service_name=s_def.name,
                            domains=s_def.all_domains,
                            target_port=port,
                            tls=self.config.proxy.tls,
                        )
                    )
            else:
                # Private Service (Internal only, no domain, no public ingress)
                console.rule(f"[bold magenta]Deploying Private Service: {s_def.name} (Isolated / Internal)[/bold magenta]")
                target_hosts = [h for r in s_def.roles for h in self.config.servers.hosts_for_role(r)]
                target_hosts = list(set(target_hosts)) or all_hosts
                for host in target_hosts:
                    await self._deploy_private_service(host, s_def, revision, full_image_tag, env_vars)

        # Step 7: Update Caddy dynamic routing for all public services
        if active_routes and web_hosts:
            await self.caddy.update_multi_service_routes(web_hosts, active_routes)

        # Step 8: Clean up stale images
        await self.remote_docker.prune(all_hosts)

        console.rule(f"[bold green]✓ Multi-container deployment complete: {self.config.service}[/bold green]")

    async def _deploy_public_service(
        self,
        host: str,
        service: ServiceDefinition,
        revision: str,
        image_tag: str,
        env_vars: Dict[str, str],
    ) -> int:
        """Roll out a public service container and return its dynamic upstream port."""
        color = self.runner.get_host_color(host)

        existing = await self.remote_docker.get_service_containers(host, service.name)
        old_container_names = [c.get("Names", "") for c in existing if c.get("Names")]

        new_port = await self.remote_docker.find_available_port(host, base_port=service.app_port)
        new_container_name = f"{service.name}-{revision}-{new_port}"

        console.print(f"[{color}][{host}][/{color}] Booting public container [cyan]{new_container_name}[/cyan] on port {new_port}...")

        labels = {
            "aayi.service": service.name,
            "aayi.role": "web",
            "aayi.public": "true",
            "aayi.revision": revision,
            "aayi.port": str(new_port),
        }

        await self.remote_docker.boot_container(
            host=host,
            name=new_container_name,
            image_tag=image_tag,
            host_port=new_port,
            container_port=service.app_port,
            env_vars=env_vars,
            labels=labels,
            command=service.command,
            network="aayi-net",
        )

        healthy = await self.remote_docker.wait_for_healthy(
            host=host,
            port=new_port,
            path=service.healthcheck_path,
            timeout=service.healthcheck_timeout,
        )

        if not healthy:
            console.print(f"[{color}][{host}][/{color}] [bold red]Health check failed for {new_container_name}. Aborting...[/bold red]")
            await self.remote_docker.stop_container(host, new_container_name)
            raise RuntimeError(f"Service '{service.name}' failed healthcheck on {host}")

        # Stop old containers after short drain
        if old_container_names:
            await asyncio.sleep(service.drain_timeout)
            for old_name in old_container_names:
                if old_name != new_container_name:
                    console.print(f"[{color}][{host}][/{color}] Stopping retired container [dim]{old_name}[/dim]...")
                    await self.remote_docker.stop_container(host, old_name)

        return new_port

    async def _deploy_private_service(
        self,
        host: str,
        service: ServiceDefinition,
        revision: str,
        image_tag: str,
        env_vars: Dict[str, str],
    ) -> None:
        """Roll out a private service container with NO host port binding."""
        color = self.runner.get_host_color(host)
        container_name = f"{service.name}-{revision}"

        # Stop previous instance of this private service
        cmd = f"docker ps -q --filter 'label=aayi.service={service.name}' | xargs -r docker stop"
        await self.runner.run_on_host(host, cmd, echo=False)

        labels = {
            "aayi.service": service.name,
            "aayi.public": "false",
            "aayi.revision": revision,
        }

        console.print(f"[{color}][{host}][/{color}] Booting private container [magenta]{container_name}[/magenta] (no host ports exposed)...")

        await self.remote_docker.boot_container(
            host=host,
            name=container_name,
            image_tag=image_tag,
            host_port=None,  # No host port!
            container_port=service.app_port,
            env_vars=env_vars,
            labels=labels,
            command=service.command,
            network="aayi-net",
        )
        console.print(f"[{color}][{host}][/{color}] [green]✓ Private service {service.name} is running securely on aayi-net.[/green]")
