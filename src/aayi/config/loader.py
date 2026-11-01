"""YAML configuration loader with environment variable substitution and Pydantic validation."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import ValidationError
from rich.console import Console

from aayi.config.models import DeployConfig

console = Console()

ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)(?::-([^}]*))?\}")


def _load_dotenv(dotenv_path: Optional[Path] = None) -> Dict[str, str]:
    """Parse a .env file into a dictionary without modifying os.environ."""
    env_vars: Dict[str, str] = {}
    if dotenv_path is None:
        dotenv_path = Path(".env")

    if not dotenv_path.is_file():
        return env_vars

    with open(dotenv_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                env_vars[key] = val
    return env_vars


def substitute_env_vars(text: str, extra_env: Optional[Dict[str, str]] = None) -> str:
    """Replace ${VAR} and ${VAR:-default} with values from os.environ or extra_env."""
    env = {**os.environ, **(extra_env or {})}

    def replace_match(match: re.Match) -> str:
        var_name = match.group(1)
        default_val = match.group(2) if match.group(2) is not None else ""
        return env.get(var_name, default_val)

    return ENV_VAR_PATTERN.sub(replace_match, text)


def load_config(config_path: Path | str = "config/deploy.yml", dotenv_path: Optional[Path | str] = None) -> DeployConfig:
    """Load, interpolate, and validate a deploy.yml file."""
    path = Path(config_path)
    if not path.is_file():
        # Also check root deploy.yml if config/deploy.yml not found
        if path == Path("config/deploy.yml") and Path("deploy.yml").is_file():
            path = Path("deploy.yml")
        else:
            raise FileNotFoundError(f"Configuration file not found at: {path}")

    extra_env = _load_dotenv(Path(dotenv_path) if dotenv_path else None)

    with open(path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    interpolated_text = substitute_env_vars(raw_text, extra_env=extra_env)

    try:
        data: Dict[str, Any] = yaml.safe_load(interpolated_text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse YAML configuration: {exc}") from exc

    try:
        return DeployConfig.model_validate(data)
    except ValidationError as exc:
        console.print(f"[bold red]Configuration validation error in {path}:[/bold red]")
        for err in exc.errors():
            loc = " -> ".join(str(x) for x in err["loc"])
            console.print(f"  [yellow]• {loc}[/yellow]: {err['msg']}")
        raise
