"""Configuration data models for Aayi deployments (Pydantic v2)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class ServerConfig(BaseModel):
    """Server topology and SSH connection configuration."""
    web: List[str] = Field(default_factory=list, description="Hosts assigned to the web role")
    workers: List[str] = Field(default_factory=list, description="Hosts assigned to background worker role")
    user: str = Field(default="root", description="Default SSH user")
    ssh_port: int = Field(default=22, description="SSH port")
    ssh_key: Optional[str] = Field(default=None, description="Path to SSH private key (.pem, id_ed25519, id_rsa)")
    ssh_keys: List[str] = Field(default_factory=list, description="List of SSH private key paths")
    ssh_password: Optional[str] = Field(default=None, description="SSH password (or env substitution ${SSH_PASSWORD})")
    passphrase: Optional[str] = Field(default=None, description="Passphrase for encrypted SSH private key")

    @field_validator("web", "workers", mode="before")
    @classmethod
    def ensure_list(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            return [v]
        return list(v or [])

    @property
    def all_hosts(self) -> List[str]:
        """Return all distinct hosts across all roles."""
        return sorted(list(set(self.web + self.workers)))

    def hosts_for_role(self, role: str) -> List[str]:
        """Return hosts for a specific role ('web', 'workers', 'all')."""
        if role == "web":
            return self.web
        elif role in ("worker", "workers"):
            return self.workers
        elif role == "all":
            return self.all_hosts
        raise ValueError(f"Unknown role '{role}'. Expected 'web', 'workers', or 'all'.")


class RegistryConfig(BaseModel):
    """Container registry credentials and settings."""
    server: str = Field(default="docker.io", description="Registry server domain")
    username: Optional[str] = Field(default=None, description="Registry username")
    password: Optional[str] = Field(default=None, description="Registry password or token")


class ProxyConfig(BaseModel):
    """Global Caddy reverse proxy and healthcheck configuration."""
    domain: Optional[str] = Field(default=None, description="Primary domain name for the service")
    domains: List[str] = Field(default_factory=list, description="Additional domain names")
    app_port: int = Field(default=8000, description="Internal port that the container listens on")
    healthcheck_path: str = Field(default="/", description="HTTP endpoint path for container health verification")
    healthcheck_timeout: int = Field(default=30, description="Max seconds to wait for container to become healthy")
    healthcheck_interval: int = Field(default=2, description="Seconds between healthcheck attempts")
    drain_timeout: int = Field(default=10, description="Seconds to wait before terminating retired containers")
    caddy_admin_port: int = Field(default=2019, description="Caddy JSON Admin API port on host")
    tls: bool = Field(default=True, description="Enable automatic TLS via Caddy")
    email: Optional[str] = Field(default=None, description="Let's Encrypt / ZeroSSL registration email")

    @property
    def all_domains(self) -> List[str]:
        result: List[str] = []
        if self.domain and self.domain not in result:
            result.append(self.domain)
        for d in self.domains:
            if d not in result:
                result.append(d)
        return result


class EnvConfig(BaseModel):
    """Environment variables and secret references."""
    clear: Dict[str, str] = Field(default_factory=dict, description="Plaintext environment variables")
    secret: List[str] = Field(default_factory=list, description="Names of secret environment variables to resolve")


class ServiceDefinition(BaseModel):
    """Configuration for an individual service container (Compose / Multi-service)."""
    name: str = Field(default="", description="Unique name of this service")
    image: Optional[str] = Field(default=None, description="Docker image repository:tag")
    build_context: Optional[str] = Field(default=None, description="Local build directory context")
    dockerfile: Optional[str] = Field(default="Dockerfile", description="Path to Dockerfile")
    public: bool = Field(default=True, description="True if exposed to public internet via Caddy domain; False if private")
    domain: Optional[str] = Field(default=None, description="Primary domain name for this container")
    domains: List[str] = Field(default_factory=list, description="Additional domain names")
    app_port: int = Field(default=8000, description="Internal container port")
    healthcheck_path: str = Field(default="/", description="HTTP path for health checking")
    healthcheck_timeout: int = Field(default=30, description="Max seconds to wait for container to be healthy")
    drain_timeout: int = Field(default=10, description="Seconds to drain in-flight traffic before stopping old container")
    env: EnvConfig = Field(default_factory=EnvConfig, description="Environment variables for this service")
    roles: List[str] = Field(default_factory=lambda: ["web"], description="Server roles where this service runs")
    command: Optional[str] = Field(default=None, description="Command override to run in container")
    depends_on: List[str] = Field(default_factory=list, description="Services that must boot before this service")

    @property
    def all_domains(self) -> List[str]:
        result: List[str] = []
        if self.domain and self.domain not in result:
            result.append(self.domain)
        for d in self.domains:
            if d not in result:
                result.append(d)
        return result


class AccessoryConfig(BaseModel):
    """Attached services like PostgreSQL, Redis, MySQL."""
    image: str = Field(description="Docker image for accessory")
    host: str = Field(description="Target host where this accessory should run")
    port: Optional[str] = Field(default=None, description="Host port mapping (e.g. '5432:5432')")
    env: Dict[str, str] = Field(default_factory=dict, description="Environment variables for accessory")
    volumes: List[str] = Field(default_factory=list, description="Volume mounts (e.g. 'postgres_data:/var/lib/postgresql/data')")


class DeployConfig(BaseModel):
    """Root deployment configuration loaded from deploy.yml or docker-compose.yml."""
    service: Optional[str] = Field(default=None, description="Default service or application name")
    image: Optional[str] = Field(default=None, description="Default base image repository")
    servers: ServerConfig = Field(description="Target servers configuration")
    registry: Optional[RegistryConfig] = Field(default=None, description="Docker registry settings")
    proxy: ProxyConfig = Field(default_factory=ProxyConfig, description="Global reverse proxy configuration")
    env: EnvConfig = Field(default_factory=EnvConfig, description="Global environment variables")
    accessories: Dict[str, AccessoryConfig] = Field(default_factory=dict, description="Accessories like Redis or Postgres")
    services: Dict[str, ServiceDefinition] = Field(default_factory=dict, description="Multi-service container definitions")

    @model_validator(mode="after")
    def validate_setup(self) -> DeployConfig:
        if not self.servers.all_hosts and not self.accessories:
            raise ValueError("Configuration must define at least one server host or accessory.")

        # Normalize single service into services dictionary for unified processing
        if not self.services and self.service and self.image:
            self.services[self.service] = ServiceDefinition(
                name=self.service,
                image=self.image,
                public=bool(self.proxy.all_domains),
                domain=self.proxy.domain,
                domains=self.proxy.domains,
                app_port=self.proxy.app_port,
                healthcheck_path=self.proxy.healthcheck_path,
                healthcheck_timeout=self.proxy.healthcheck_timeout,
                drain_timeout=self.proxy.drain_timeout,
                env=self.env,
                roles=["web"],
            )
        elif self.services:
            for s_name, s_def in self.services.items():
                if not s_def.name:
                    s_def.name = s_name
            if not self.service:
                self.service = next(iter(self.services.keys()))
        elif not self.accessories:
            raise ValueError("Configuration must define either 'service' & 'image' or a 'services' mapping.")

        return self
