<p align="center">
  <img src="assets/banner.jpeg" alt="Aayi - Modern Cloud Deployment Dev Tool" width="100%">
</p>

<p align="center">
  <strong>Deploy Docker apps to any server with zero downtime and automatic HTTPS.</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/aayi/"><img src="https://img.shields.io/pypi/v/aayi.svg?color=blue&style=flat-square" alt="PyPI version"></a>
  <a href="https://pypi.org/project/aayi/"><img src="https://img.shields.io/pypi/pyversions/aayi.svg?style=flat-square" alt="Python Versions"></a>
  <a href="https://github.com/thlurte/aayi/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/thlurte/aayi/ci.yml?branch=main&label=CI&style=flat-square" alt="CI Status"></a>
  <a href="https://github.com/thlurte/aayi/releases"><img src="https://img.shields.io/github/v/release/thlurte/aayi?style=flat-square&color=success" alt="GitHub Release"></a>
  <a href="https://github.com/thlurte/aayi/stargazers"><img src="https://img.shields.io/github/stars/thlurte/aayi?style=flat-square&logo=github" alt="GitHub stars"></a>
  <a href="https://github.com/thlurte/aayi/blob/main/LICENSE"><img src="https://img.shields.io/github/license/thlurte/aayi.svg?style=flat-square" alt="License: MIT"></a>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> &bull;
  <a href="#features">Features</a> &bull;
  <a href="#command-reference">Commands</a>
</p>


## Features

- **Zero-Downtime Container Swaps**: Seamlessly switches traffic between revisions via Caddy's dynamic in-memory Admin API (`:2019`) with active health checks.
- **Automatic HTTPS**: TLS certificates via Let's Encrypt / ZeroSSL managed automatically by Caddy without external cert-bot scripts.
- **Docker Compose & Multi-Container**: Deploy multi-service setups directly from `docker-compose.yml` or multi-service `deploy.yml`.
- **Public vs. Private Containers**:
  - **Public**: Dedicated subdomains per service (`app.example.com`, `api.example.com`) with automatic TLS.
  - **Private**: Background workers, redis, databases on isolated internal network `aayi-net` with zero exposed host ports.
- **Concurrent SSH Execution**: Asynchronous, parallel multi-server deployment fleet powered by AsyncSSH.
- **Type-Safe Declarative Config**: Strict validation of `config/deploy.yml` with friendly Pydantic error diagnostics and `${ENV_VAR:-default}` substitution.
- **Remote Builds Without Registry**: Build directly on remote servers using in-memory tar stream (`--remote-build`) without needing Docker Hub or AWS ECR.
- **Instant Rollback**: Revert traffic to previous healthy container in milliseconds with `aayi rollback`.
- **Stateful Accessories**: Spin up databases (PostgreSQL, MySQL, Redis) with persistent Docker volumes and loopback host protection.


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
  user: ubuntu          # Default SSH user (e.g. root, ubuntu, ec2-user, debian)
  ssh_port: 22          # Optional: custom SSH port
  
  # Option A: SSH Key / AWS .pem file
  ssh_key: ~/.ssh/my-server-key.pem

  # Option B: SSH Password (supports env var substitution)
  # ssh_password: ${SSH_PASSWORD}

  # Option C: Passphrase (if your private key is encrypted)
  # passphrase: ${SSH_KEY_PASSPHRASE}

registry:
  server: docker.io
  username: your-docker-user
  password: ${DOCKER_REGISTRY_PASSWORD}

# Global proxy config (for single-container apps)
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

#### Or Multi-Service Configuration 

You can define multiple services in `config/deploy.yml`, specifying exactly which ones are **public** with their own domain/subdomain, and which ones are **private**:

```yaml
services:
  # Public web frontend with its own subdomain & automatic HTTPS
  web:
    image: your-docker-user/myweb
    public: true
    domain: app.example.com
    app_port: 3000
    roles: [web]

  # Public API microservice with a separate subdomain
  api:
    image: your-docker-user/myapi
    public: true
    domain: api.example.com
    app_port: 8000
    roles: [web]

  # Private background worker (no domain, zero open ports, internal aayi-net only)
  worker:
    image: your-docker-user/myworker
    public: false
    command: python -m worker
    roles: [workers]
    depends_on: [api]
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


## Workflows

### 1. Docker Compose Integration

If your application is already defined in a `docker-compose.yml`, Aayi can parse and convert it directly into a zero-downtime, multi-service deployment setup:

```bash
# Convert docker-compose.yml into config/deploy.yml
aayi compose convert --output config/deploy.yml
```

Or deploy directly from compose without generating files:
```bash
aayi deploy --compose docker-compose.yml
```

- **Public Web Services**: Services with exposed ports (e.g. `80:8000`) get automatic Caddy routes, domains, and HTTPS certificates.
- **Private Microservices & Workers**: Services without host ports (e.g., Celery, Redis, background jobs) are isolated on the secure `aayi-net` bridge network so they communicate internally with zero external exposure.
- **Dependencies (`depends_on`)**: Preserves startup order so databases/caches boot before dependent web servers.

### 2. Override Domain and Public/Private Visibility from CLI

You can override any container's public domain or switch it between public and private directly when deploying from the terminal:

```bash
# Expose container on a specific subdomain with automatic TLS:
aayi deploy --domain api.example.com --public

# Deploy a specific microservice with its own subdomain:
aayi deploy --service analytics --domain stats.example.com --public

# Force a container to be completely private (isolated to internal network):
aayi deploy --service worker --private
```

### 3. Registry Options: Cloud, Local/Self-Hosted, or Registry-Free

Aayi supports three flexible registry modes:

#### Cloud Registries (Docker Hub, GitHub GHCR, AWS ECR, GCP Artifact Registry)
```yaml
registry:
  server: ghcr.io  # or docker.io, 123456789.dkr.ecr.us-east-1.amazonaws.com
  username: your-username
  password: ${REGISTRY_TOKEN}
```

#### Self-Hosted & Local Private Registries (`registry:2` on LAN or VPN)
If you run a local Docker registry inside your private network or on a local server (e.g. `192.168.1.50:5000` or `registry.internal:5000`):
```yaml
image: registry.internal:5000/myapp
registry:
  server: registry.internal:5000
  # Username/password can be omitted if your internal registry requires no auth:
  username: null
  password: null
```

#### Zero-Registry Mode (Ephemeral Remote Build)
Don't want to run or pay for any container registry? Deploy code directly over SSH:
```bash
aayi deploy --remote-build
```
Aayi packages your code directory into an in-memory tar stream, pipes it across the AsyncSSH tunnel directly to the target server's Docker daemon, and builds it on-server without ever uploading an image to the internet.


## Command Reference

| Command | Description |
| :--- | :--- |
| `aayi init` | Scaffolds `config/deploy.yml`, `Dockerfile`, and `.dockerignore`. |
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
| `aayi compose convert` | Convert an existing `docker-compose.yml` to native `config/deploy.yml`. |
| `aayi ci init` | Generates `.github/workflows/deploy.yml` for automated GitHub Actions CI/CD. |
| `aayi version` | Display current installed version of Aayi. |


## License

MIT
