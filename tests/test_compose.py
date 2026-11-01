"""Unit tests for Docker Compose parsing and multi-service public/private isolation."""

from pathlib import Path
import pytest

from aayi.compose.parser import convert_compose_to_deploy_yaml, parse_compose_file


SAMPLE_COMPOSE = """
version: '3.8'

services:
  web:
    image: myuser/web:latest
    ports:
      - "3000:3000"
    labels:
      - "aayi.domain=app.example.com"
      - "aayi.public=true"
      - "aayi.port=3000"
      - "aayi.healthcheck=/healthz"
    environment:
      - NODE_ENV=production
      - API_URL=http://api:8000
    depends_on:
      - api

  api:
    image: myuser/api:latest
    ports:
      - "8000"
    labels:
      - "aayi.domain=api.example.com"
      - "aayi.domains=api-v1.example.com"
      - "aayi.public=true"
      - "aayi.port=8000"
      - "aayi.healthcheck=/health"
    environment:
      - DATABASE_URL=postgres://user:pass@db:5432/mydb
    depends_on:
      - worker

  worker:
    image: myuser/worker:latest
    labels:
      - "aayi.public=false"
    environment:
      - REDIS_URL=redis://redis:6379
    command: ["python", "worker.py"]
"""


def test_parse_compose_file_public_and_private(tmp_path: Path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(SAMPLE_COMPOSE, encoding="utf-8")

    cfg = parse_compose_file(compose_file)

    assert "web" in cfg.services
    assert "api" in cfg.services
    assert "worker" in cfg.services

    # Web service assertions (Public)
    web = cfg.services["web"]
    assert web.public is True
    assert web.domain == "app.example.com"
    assert web.app_port == 3000
    assert web.healthcheck_path == "/healthz"
    assert web.depends_on == ["api"]

    # API service assertions (Public with multiple domains)
    api = cfg.services["api"]
    assert api.public is True
    assert api.domain == "api.example.com"
    assert api.all_domains == ["api.example.com", "api-v1.example.com"]
    assert api.app_port == 8000

    # Worker service assertions (Private)
    worker = cfg.services["worker"]
    assert worker.public is False
    assert worker.all_domains == []
    assert worker.command == "python worker.py"


def test_convert_compose_to_deploy_yaml(tmp_path: Path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(SAMPLE_COMPOSE, encoding="utf-8")

    yaml_output = convert_compose_to_deploy_yaml(compose_file, web_host="10.0.0.5")

    assert "10.0.0.5" in yaml_output
    assert "app.example.com" in yaml_output
    assert "api.example.com" in yaml_output
    assert "worker:" in yaml_output
