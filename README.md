<p align="center">
  <img src="assets/banner.jpeg" alt="Aayi - Modern Cloud Deployment Dev Tool" width="100%">
</p>

# Aayi

Deploy Docker apps to any server with zero downtime and automatic HTTPS.

Built with uv, AsyncSSH, Typer, Pydantic, and Caddy.

---

## Features

- **Zero-Downtime Container Swaps**: Routes traffic between container revisions via Caddy's dynamic in-memory Admin API (`:2019`).
- **Automatic HTTPS**: Full TLS certificate lifecycle via Let's Encrypt / ZeroSSL powered by Caddy.
- **Docker Compose & Multi-Container**: Deploy multi-service applications directly from `docker-compose.yml` or multi-service `deploy.yml`.
- **Public vs. Private Containers**:
  - **Public**: Dedicated domain per container (e.g. `app.example.com` and `api.example.com`) with automatic Caddy TLS.
  - **Private**: Background workers, databases, and microservices connected to internal `aayi-net` with no host port exposure.
- **Concurrent SSH Execution**: Asynchronous, parallel multi-server operations powered by AsyncSSH.
- **Type-Safe Declarative Config**: Strict validation of `config/deploy.yml` with friendly Pydantic v2 error messages.
- **GitHub Actions Ready**: Out-of-the-box CI/CD generator (`aayi ci init`).
- **No Ruby Required**: Built for developers who want Kamal's simplicity without installing Ruby or gem bundlers.
- **Stateful Accessories**: Spin up and manage PostgreSQL, Redis, or MySQL containers with persistent Docker volumes.
- **Instant Rollback**: Revert Caddy upstreams back to the prior container in milliseconds with `aayi rollback`.

---

## Architecture

```
                                 [ CLI: aayi ]
                               (Typer + Rich UI)
                                       │
                              [ Config: deploy.yml ]
                               (Pydantic v2 Models)
                                       │
                      ┌────────────────┴────────────────┐
                      ▼                                 ▼
             [ Local Builder ]                 [ AsyncSSH Fleet ]
           (docker buildx push)             (Parallel Multi-Server Pool)
                                                        │
                              ┌─────────────────────────┴─────────────────────────┐
                              ▼                                                   ▼
                       [ Web Server(s) ]                                   [ Worker Server(s) ]
                     ┌───────────────────┐                               ┌────────────────────┐
                     │ Caddy (Port 80/443│                               │ Worker Container   │
                     │  Admin API :2019) │                               └────────────────────┘
                     └─────────┬─────────┘
                               │ (Dynamic Route)
                     ┌─────────┴─────────┐
                     │ App Container     │
                     │ (Port: 8000/8001) │
                     └───────────────────┘
```

---

## Quickstart

### 1. Installation

**One-line standalone installer (Zero dependencies, no Python or pip needed):**
```bash
curl -fsSL https://raw.githubusercontent.com/thlurte/aayi/main/install.sh | bash
```

**Or run instantly with uvx (no installation required):**
```bash
uvx aayi --help
```

**Or install via uv tool or pip:**
```bash
uv tool install aayi
# or
pip install aayi
```

### 2. Initialize Your Project

Generate starter `config/deploy.yml` and an optimized multi-stage `Dockerfile`:
```bash
aayi init
```

### 3. Configure `config/deploy.yml`

```yaml
service: myapp
image: your-docker-user/myapp

servers:
  web:
    - 192.168.1.10
  workers:
    - 192.168.1.11
  user: root

registry:
  server: docker.io
  username: your-docker-user
  password: ${DOCKER_REGISTRY_PASSWORD}

proxy:
  domain: app.example.com
  app_port: 8000
  healthcheck_path: /healthz
  drain_timeout: 10
  tls: true
  email: admin@example.com

env:
  clear:
    PORT: "8000"
    ENVIRONMENT: production
  secret:
    - SECRET_KEY
    - DATABASE_URL
```

### 4. Setup Target Servers

Ensures official Docker CE and Caddy are running on all target hosts:
```bash
aayi setup
```

### 5. Deploy with Zero Downtime

Builds the image with `docker buildx`, pushes to your registry, boots a new container, runs health checks, and smoothly switches Caddy upstream traffic:
```bash
aayi deploy
```

---

## Command Reference

| Command | Description |
| :--- | :--- |
| `aayi init` | Scaffolds `config/deploy.yml`, `Dockerfile`, and `.dockerignore`. |
| `aayi ci init` | Generates `.github/workflows/deploy.yml` for automated GitHub Actions CI/CD. |
| `aayi setup` | Installs Docker CE (if missing) and boots Caddy reverse proxy on all hosts. |
| `aayi deploy` | Full zero-downtime deployment (build, push, boot, healthcheck, swap Caddy traffic). |
| `aayi redeploy` | Redeploy an existing image tag on remote servers without rebuilding locally. |
| `aayi rollback` | Atomically revert Caddy upstream to the previous healthy container. |
| `aayi logs` | Real-time multi-host unified log streaming with colored host labels. |
| `aayi exec <cmd>` | Execute an ad-hoc command or interactive shell inside running container. |
| `aayi proxy status` | Inspect current live Caddy routing state and upstreams. |
| `aayi proxy reload` | Force Caddy to route traffic to a specific container port. |
| `aayi accessory boot <name>` | Start an accessory (e.g. Postgres, Redis) with persistent volumes. |
| `aayi accessory stop <name>` | Stop an accessory container. |
| `aayi accessory logs <name>` | View or follow logs for an accessory container. |

---

## Running Tests

```bash
uv run pytest -v
```

---

## License

MIT
