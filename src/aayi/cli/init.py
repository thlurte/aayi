"""Project initialization and scaffolding generator for Aayi."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

console = Console()

DEPLOY_YML_TEMPLATE = """# Aayi Deployment Configuration
service: myapp
image: your-docker-user/myapp

# Server topology
servers:
  web:
    - 192.168.1.10
  workers:
    - 192.168.1.11
  user: root
  ssh_port: 22

# Docker container registry
registry:
  server: docker.io
  username: your-docker-user
  password: ${DOCKER_REGISTRY_PASSWORD}

# Caddy Reverse Proxy & Zero-Downtime Traffic Routing
proxy:
  domain: app.example.com
  app_port: 8000
  healthcheck_path: /healthz
  healthcheck_timeout: 30
  drain_timeout: 10
  tls: true
  email: admin@example.com

# Environment variables
env:
  clear:
    PORT: "8000"
    ENVIRONMENT: production
  secret:
    - SECRET_KEY_BASE
    - DATABASE_URL

# Attached stateful accessories (optional)
accessories:
  postgres:
    image: postgres:16-alpine
    host: 192.168.1.10
    port: "5432:5432"
    env:
      POSTGRES_DB: myapp_production
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    host: 192.168.1.10
    port: "6379:6379"
    volumes:
      - redis_data:/data
"""

DOCKERFILE_TEMPLATE = """# Multi-stage Dockerfile optimized with uv
FROM python:3.13-slim AS builder

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

FROM python:3.13-slim

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY . .

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""

DOCKERIGNORE_TEMPLATE = """.git
.venv
__pycache__
*.pyc
.env
.pytest_cache
dist
build
"""


def init_project(force: bool = False) -> None:
    """Scaffold initial deployment configuration and Dockerfile."""
    config_dir = Path("config")
    config_dir.mkdir(exist_ok=True)
    deploy_yml = config_dir / "deploy.yml"

    if deploy_yml.exists() and not force:
        console.print(f"[yellow]Skipped {deploy_yml} (already exists). Use --force to overwrite.[/yellow]")
    else:
        deploy_yml.write_text(DEPLOY_YML_TEMPLATE, encoding="utf-8")
        console.print(f"[green]✓ Created {deploy_yml}[/green]")

    dockerfile = Path("Dockerfile")
    if not dockerfile.exists() or force:
        dockerfile.write_text(DOCKERFILE_TEMPLATE, encoding="utf-8")
        console.print(f"[green]✓ Created {dockerfile}[/green]")

    dockerignore = Path(".dockerignore")
    if not dockerignore.exists() or force:
        dockerignore.write_text(DOCKERIGNORE_TEMPLATE, encoding="utf-8")
        console.print(f"[green]✓ Created {dockerignore}[/green]")

    console.print("\n[bold green]Project initialized for Aayi![/bold green]")
    console.print("Next steps:")
    console.print("  1. Edit [cyan]config/deploy.yml[/cyan] with your server IP and domain.")
    console.print("  2. Set up target servers: [bold]aayi setup[/bold]")
    console.print("  3. Deploy your application: [bold]aayi deploy[/bold]\n")
