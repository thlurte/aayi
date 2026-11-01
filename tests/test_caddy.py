"""Unit tests for Caddy reverse proxy manager and Caddyfile generation."""

from aayi.config.models import ProxyConfig
from aayi.proxy.caddy import CaddyManager
from aayi.ssh.runner import SSHFleetRunner


def test_generate_caddyfile():
    runner = SSHFleetRunner()
    proxy_cfg = ProxyConfig(
        domain="app.example.com",
        domains=["api.example.com"],
        app_port=8000,
        tls=True,
        email="ops@example.com",
    )
    manager = CaddyManager(runner, proxy_cfg)

    caddyfile = manager.generate_caddyfile(target_port=8001)

    assert "admin 127.0.0.1:2019" in caddyfile
    assert "email ops@example.com" in caddyfile
    assert "app.example.com api.example.com {" in caddyfile
    assert "reverse_proxy 127.0.0.1:8001" in caddyfile
    assert "lb_try_duration 5s" in caddyfile


def test_generate_caddyfile_tls_off():
    runner = SSHFleetRunner()
    proxy_cfg = ProxyConfig(
        domain="local.test",
        tls=False,
    )
    manager = CaddyManager(runner, proxy_cfg)
    caddyfile = manager.generate_caddyfile(target_port=8080)

    assert "local.test {" in caddyfile
    assert "tls off" in caddyfile
    assert "reverse_proxy 127.0.0.1:8080" in caddyfile
