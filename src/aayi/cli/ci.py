"""GitHub Actions CI/CD workflow generator for Aayi."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

console = Console()

GITHUB_WORKFLOW_TEMPLATE = """name: Deploy Application

on:
  push:
    branches:
      - main

jobs:
  deploy:
    name: Deploy to Production
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          version: "latest"

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to Docker Registry
        uses: docker/login-action@v3
        with:
          registry: ${{ secrets.DOCKER_REGISTRY || 'docker.io' }}
          username: ${{ secrets.DOCKER_USERNAME }}
          password: ${{ secrets.DOCKER_PASSWORD }}

      - name: Set up SSH Private Key
        uses: webfactory/ssh-agent@v0.9.0
        with:
          ssh-private-key: ${{ secrets.SSH_PRIVATE_KEY }}

      - name: Add Known Hosts
        run: |
          mkdir -p ~/.ssh
          # You can add: ssh-keyscan -H <YOUR_SERVER_IP> >> ~/.ssh/known_hosts

      - name: Deploy with Aayi
        env:
          DOCKER_REGISTRY_PASSWORD: ${{ secrets.DOCKER_PASSWORD }}
        run: |
          uvx aayi deploy
"""


def init_github_actions(force: bool = False) -> Path:
    """Generate .github/workflows/deploy.yml for automated deployments."""
    workflows_dir = Path(".github") / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    workflow_file = workflows_dir / "deploy.yml"

    if workflow_file.exists() and not force:
        console.print(f"[yellow]Skipped {workflow_file} (already exists). Use --force to overwrite.[/yellow]")
    else:
        workflow_file.write_text(GITHUB_WORKFLOW_TEMPLATE, encoding="utf-8")
        console.print(f"[green]✓ Created GitHub Actions workflow at {workflow_file}[/green]")

    console.print("\n[bold cyan]GitHub Actions setup tips:[/bold cyan]")
    console.print("  Configure these repository secrets in GitHub ([bold]Settings > Secrets and variables > Actions[/bold]):")
    console.print("  • [yellow]SSH_PRIVATE_KEY[/yellow]: The private SSH key authorized on your target servers.")
    console.print("  • [yellow]DOCKER_USERNAME[/yellow]: Your Docker Hub or container registry username.")
    console.print("  • [yellow]DOCKER_PASSWORD[/yellow]: Your container registry password or access token.\n")
    return workflow_file
