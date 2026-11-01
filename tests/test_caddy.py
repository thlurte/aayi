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


def test_generate_multi_service_caddyfile_with_private_containers():
    from aayi.proxy.caddy import ServiceRoute

    runner = SSHFleetRunner()
    proxy_cfg = ProxyConfig(email="admin@example.com")
    manager = CaddyManager(runner, proxy_cfg)

    routes = [
        ServiceRoute(service_name="web", domains=["app.example.com"], target_port=3000),
        ServiceRoute(service_name="api", domains=["api.example.com", "v1.example.com"], target_port=8000),
        # Private service has no domains and is not routed in Caddy
        ServiceRoute(service_name="worker", domains=[], target_port=0),
    ]

    caddyfile = manager.generate_multi_service_caddyfile(routes)

    # Public services are present
    assert "app.example.com {" in caddyfile
    assert "reverse_proxy 127.0.0.1:3000" in caddyfile
    assert "api.example.com v1.example.com {" in caddyfile
    assert "reverse_proxy 127.0.0.1:8000" in caddyfile

    # Private service is completely omitted from Caddyfile
    assert "worker {" not in caddyfile
    assert "127.0.0.1:0" not in caddyfile

