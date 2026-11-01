"""Unit tests for configuration models and YAML loader."""

from pathlib import Path
import pytest
from pydantic import ValidationError

from aayi.config.loader import load_config, substitute_env_vars
from aayi.config.models import DeployConfig, ProxyConfig, ServerConfig


def test_server_config_roles():
    cfg = ServerConfig(
        web=["192.168.1.10", "192.168.1.11"],
        workers=["192.168.1.12"],
        user="deploy",
        ssh_port=2222,
    )
    assert cfg.all_hosts == ["192.168.1.10", "192.168.1.11", "192.168.1.12"]
    assert cfg.hosts_for_role("web") == ["192.168.1.10", "192.168.1.11"]
    assert cfg.hosts_for_role("workers") == ["192.168.1.12"]
    assert cfg.hosts_for_role("all") == cfg.all_hosts

    with pytest.raises(ValueError):
        cfg.hosts_for_role("invalid_role")


def test_proxy_domains():
    proxy = ProxyConfig(domain="app.example.com", domains=["api.example.com", "app.example.com"])
    assert proxy.all_domains == ["app.example.com", "api.example.com"]


def test_env_var_substitution():
    template = "password: ${SECRET_KEY:-default_secret} and ${NON_EXISTENT:-fallback}"
    res = substitute_env_vars(template, extra_env={"SECRET_KEY": "my_super_secret"})
    assert res == "password: my_super_secret and fallback"


def test_load_valid_config(tmp_path: Path):
    yaml_content = """
service: testapp
image: testuser/testapp
servers:
  web:
    - 10.0.0.1
  user: ubuntu
proxy:
  domain: test.example.com
  app_port: 3000
  healthcheck_path: /health
env:
  clear:
    NODE_ENV: production
  secret:
    - DB_PASSWORD
"""
    config_file = tmp_path / "deploy.yml"
    config_file.write_text(yaml_content, encoding="utf-8")

    cfg = load_config(config_file)
    assert cfg.service == "testapp"
    assert cfg.image == "testuser/testapp"
    assert cfg.servers.web == ["10.0.0.1"]
    assert cfg.servers.user == "ubuntu"
    assert cfg.proxy.domain == "test.example.com"
    assert cfg.proxy.app_port == 3000
    assert cfg.proxy.healthcheck_path == "/health"
    assert cfg.env.clear == {"NODE_ENV": "production"}
    assert cfg.env.secret == ["DB_PASSWORD"]


def test_invalid_config_raises_error(tmp_path: Path):
    # Missing servers/hosts
    yaml_content = """
service: badapp
image: baduser/badapp
servers:
  web: []
"""
    config_file = tmp_path / "deploy.yml"
    config_file.write_text(yaml_content, encoding="utf-8")

    with pytest.raises(ValidationError):
        load_config(config_file)


def test_env_config_scalar_and_null_coercion():
    from aayi.config.models import EnvConfig

    raw_env = {
        "PORT": 8080,
        "DEBUG": True,
        "RATIO": 3.14,
        "EMPTY": None,
        "STRING": "already_str",
    }
    cfg = EnvConfig(clear=raw_env)
    assert cfg.clear["PORT"] == "8080"
    assert cfg.clear["DEBUG"] == "True"
    assert cfg.clear["RATIO"] == "3.14"
    assert cfg.clear["EMPTY"] == ""
    assert cfg.clear["STRING"] == "already_str"

