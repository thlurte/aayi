"""Full zero-downtime multi-container deployment orchestration pipeline."""

from __future__ import annotations

import asyncio
import os
from typing import Dict, List, Optional

from rich.console import Console

from aayi.config.models import DeployConfig, ServiceDefinition
from aayi.docker.builder import DockerBuilder, get_git_revision
from aayi.docker.remote import RemoteDocker
from aayi.docker.tar_builder import RemoteTarBuilder
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
            default_keys=config.servers.ssh_keys,
            password=config.servers.ssh_password,
            passphrase=config.servers.passphrase,
        )
        self.remote_docker = RemoteDocker(self.runner)
        self.caddy = CaddyManager(self.runner, config.proxy)
        self.builder = DockerBuilder()
        self.tar_builder = RemoteTarBuilder(self.runner)

    @staticmethod
    def resolve_image_tag(image_base: str, tag: Optional[str] = None, default_revision: str = "latest") -> str:
        """Resolve full image tag without creating duplicate tags (e.g. avoid image:prod:latest)."""
        if ":" in image_base:
            base_part, existing_tag = image_base.split(":", 1)
            return f"{base_part}:{tag or existing_tag}"
        return f"{image_base}:{tag or default_revision}"

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
        remote_build: bool = False,
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
        if not remote_build:
            await self.remote_docker.login_registry(all_hosts, self.config.registry)

        # Step 3: Build step (Remote tar streaming vs Local buildx)
        if remote_build:
            console.rule("[bold cyan]Remote Build: Streaming Source via In-Memory Tar Archive[/bold cyan]")
            for s_def in ordered_services:
                image_base = s_def.image or self.config.image or s_def.name
                full_image_tag = self.resolve_image_tag(image_base, tag, revision)
                for host in all_hosts:
                    await self.tar_builder.stream_and_build(
                        host=host,
                        service=s_def.name,
                        image_tag=full_image_tag,
                        context=s_def.build_context or ".",
                        dockerfile=s_def.dockerfile or "Dockerfile",
                    )
        else:
            # Local build & push
            for s_def in ordered_services:
                image_base = s_def.image or self.config.image
                if not image_base:
                    continue
                if build and (s_def.build_context or s_def.dockerfile):
                    self.builder.build_and_push(
                        image_base=image_base,
                        tag=tag or revision,
                        dockerfile=s_def.dockerfile or "Dockerfile",
                        context=s_def.build_context or ".",
                        push=push,
                    )

            for s_def in ordered_services:
                image_base = s_def.image or self.config.image
                if image_base:
                    full_image_tag = self.resolve_image_tag(image_base, tag, revision)
                    is_local_build = bool(build and (s_def.build_context or s_def.dockerfile))
                    is_direct = is_local_build and (not self.config.registry or not self.config.registry.server or not push)
                    if is_direct:
                        await self.remote_docker.stream_image_to_hosts(all_hosts, full_image_tag)
                    else:
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
            full_image_tag = self.resolve_image_tag(image_base, tag, revision)
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
        if web_hosts:
            # Preserve running routes for services in deploy.yml that were not targeted in this run
            deployed_service_names = {r.service_name for r in active_routes}
            for s_name, s_def in self.config.services.items():
                if s_name not in deployed_service_names and s_def.public and s_def.all_domains:
                    host = web_hosts[0]
                    check_cmd = f"docker inspect -f '{{{{index .Config.Labels \"aayi.port\"}}}}' $(docker ps -q --filter 'label=aayi.service={s_name}' | head -n 1) 2>/dev/null || true"
                    res = await self.runner.run_on_host(host, check_cmd, echo=False)
                    port_str = res.stdout.strip()
                    if port_str and port_str.isdigit():
                        active_routes.append(
                            ServiceRoute(
                                service_name=s_name,
                                domains=s_def.all_domains,
                                target_port=int(port_str),
                                tls=self.config.proxy.tls,
                            )
                        )

            if active_routes:
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
            volumes=service.volumes,
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
            volumes=service.volumes,
            network="aayi-net",
        )
        console.print(f"[{color}][{host}][/{color}] [green]✓ Private service {service.name} is running securely on aayi-net.[/green]")
