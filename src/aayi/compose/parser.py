"""Docker Compose file parser and adapter for Aayi."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from aayi.config.models import DeployConfig, EnvConfig, ServerConfig, ServiceDefinition


def _parse_compose_env(env_data: Any) -> EnvConfig:
    """Parse environment block from Docker Compose into clear and secret env vars."""
    clear: Dict[str, str] = {}
    secret: List[str] = []

    if isinstance(env_data, list):
        for item in env_data:
            if isinstance(item, str):
                if "=" in item:
                    k, v = item.split("=", 1)
                    clear[k.strip()] = v.strip().strip("'\"")
                else:
                    secret.append(item.strip())
    elif isinstance(env_data, dict):
        for k, v in env_data.items():
            if v is None or v == "":
                secret.append(str(k))
            else:
                clear[str(k)] = str(v)

    return EnvConfig(clear=clear, secret=secret)


def _parse_compose_labels(labels_data: Any) -> Dict[str, str]:
    """Parse labels block from Docker Compose into a dictionary."""
    labels: Dict[str, str] = {}
    if isinstance(labels_data, list):
        for item in labels_data:
            if isinstance(item, str) and "=" in item:
                k, v = item.split("=", 1)
                labels[k.strip()] = v.strip().strip("'\"")
    elif isinstance(labels_data, dict):
        for k, v in labels_data.items():
            labels[str(k)] = str(v)
    return labels


def parse_compose_file(
    compose_path: Path | str,
    servers: Optional[ServerConfig] = None,
) -> DeployConfig:
    """Parse a docker-compose.yml into an Aayi DeployConfig with multi-service definitions."""
    path = Path(compose_path)
    if not path.is_file():
        raise FileNotFoundError(f"Docker Compose file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data: Dict[str, Any] = yaml.safe_load(f) or {}

    raw_services: Dict[str, Any] = data.get("services", {})
    if not raw_services:
        raise ValueError(f"No services found in compose file: {path}")

    parsed_services: Dict[str, ServiceDefinition] = {}

    for service_name, s_data in raw_services.items():
        if not isinstance(s_data, dict):
            continue

        labels = _parse_compose_labels(s_data.get("labels", []))
        env = _parse_compose_env(s_data.get("environment", {}))

        # Check domain configuration
        domain = labels.get("aayi.domain")
        extra_domains_raw = labels.get("aayi.domains", "")
        extra_domains = [d.strip() for d in extra_domains_raw.split(",") if d.strip()]

        # Determine if public: default to True if domain is specified, or if aayi.public=true
        if "aayi.public" in labels:
            is_public = labels["aayi.public"].lower() in ("true", "1", "yes")
        else:
            is_public = bool(domain or extra_domains)

        # Determine app port
        port = 8000
        if "aayi.port" in labels:
            try:
                port = int(labels["aayi.port"])
            except ValueError:
                port = 8000
        elif s_data.get("ports"):
            first_port = str(s_data["ports"][0])
            # handle '8000:8000' or '8000'
            parts = first_port.split(":")
            try:
                port = int(parts[-1].split("/")[0])
            except ValueError:
                port = 8000

        # Build context / dockerfile
        build_context: Optional[str] = None
        dockerfile: Optional[str] = None
        if "build" in s_data:
            build_raw = s_data["build"]
            if isinstance(build_raw, str):
                build_context = build_raw
                dockerfile = "Dockerfile"
            elif isinstance(build_raw, dict):
                build_context = build_raw.get("context", ".")
                dockerfile = build_raw.get("dockerfile", "Dockerfile")

        # Healthcheck
        healthcheck_path = labels.get("aayi.healthcheck", "/")

        # Roles
        roles_raw = labels.get("aayi.roles", "web")
        roles = [r.strip() for r in roles_raw.split(",") if r.strip()]

        # Depends on
        depends_on: List[str] = []
        raw_deps = s_data.get("depends_on", [])
        if isinstance(raw_deps, list):
            depends_on = [str(d) for d in raw_deps]
        elif isinstance(raw_deps, dict):
            depends_on = [str(d) for d in raw_deps.keys()]

        command = s_data.get("command")
        if isinstance(command, list):
            command = " ".join(command)

        raw_volumes = s_data.get("volumes", [])
        volumes = [str(v) for v in raw_volumes] if isinstance(raw_volumes, list) else []

        parsed_services[service_name] = ServiceDefinition(
            name=service_name,
            image=s_data.get("image"),
            build_context=build_context,
            dockerfile=dockerfile,
            public=is_public,
            domain=domain,
            domains=extra_domains,
            app_port=port,
            healthcheck_path=healthcheck_path,
            env=env,
            roles=roles,
            command=command,
            depends_on=depends_on,
            volumes=volumes,
        )

    # If servers are not provided, default to localhost or placeholder
    server_cfg = servers or ServerConfig(web=["127.0.0.1"])

    return DeployConfig(
        service=next(iter(parsed_services.keys())),
        servers=server_cfg,
        services=parsed_services,
    )


def convert_compose_to_deploy_yaml(
    compose_path: Path | str,
    web_host: str = "192.168.1.10",
) -> str:
    """Generate a clean config/deploy.yml string from a Docker Compose file."""
    cfg = parse_compose_file(compose_path, servers=ServerConfig(web=[web_host]))

    export_dict: Dict[str, Any] = {
        "servers": {
            "web": [web_host],
            "user": "root",
            "ssh_port": 22,
        },
        "services": {},
    }

    for s_name, s_def in cfg.services.items():
        s_dict: Dict[str, Any] = {
            "public": s_def.public,
            "app_port": s_def.app_port,
        }
        if s_def.image:
            s_dict["image"] = s_def.image
        if s_def.build_context:
            s_dict["build_context"] = s_def.build_context
        if s_def.dockerfile and s_def.dockerfile != "Dockerfile":
            s_dict["dockerfile"] = s_def.dockerfile
        if s_def.public and s_def.all_domains:
            s_dict["domain"] = s_def.domain
            if s_def.domains:
                s_dict["domains"] = s_def.domains
            s_dict["healthcheck_path"] = s_def.healthcheck_path
        if s_def.env.clear:
            s_dict["env"] = {"clear": s_def.env.clear}
        if s_def.depends_on:
            s_dict["depends_on"] = s_def.depends_on
        if s_def.command:
            s_dict["command"] = s_def.command

        export_dict["services"][s_name] = s_dict

    return yaml.dump(export_dict, sort_keys=False)
