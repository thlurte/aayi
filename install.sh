#!/usr/bin/env bash
# Aayi Installer Script
# Usage: curl -fsSL https://raw.githubusercontent.com/thlurte/aayi/main/install.sh | bash

set -euo pipefail

REPO="${AAYI_REPO:-thlurte/aayi}"
INSTALL_DIR="${AAYI_INSTALL_DIR:-${HOME}/.local/bin}"

# Detect OS
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "${OS}" in
  linux*)  OS="linux" ;;
  darwin*) OS="darwin" ;;
  *) echo "Error: Unsupported operating system: ${OS}"; exit 1 ;;
esac

# Detect Architecture
ARCH="$(uname -m)"
case "${ARCH}" in
  x86_64|amd64) ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *) echo "Error: Unsupported architecture: ${ARCH}"; exit 1 ;;
esac

BINARY_NAME="aayi-${OS}-${ARCH}"
DOWNLOAD_URL="https://github.com/${REPO}/releases/latest/download/${BINARY_NAME}"

echo "Installing Aayi (${OS}/${ARCH})..."
mkdir -p "${INSTALL_DIR}"

if [ "${AAYI_DRY_RUN:-0}" = "1" ]; then
  echo "Dry run successful: target binary is ${BINARY_NAME} from ${DOWNLOAD_URL}"
  exit 0
fi

DOWNLOAD_SUCCESS=0

if command -v curl >/dev/null 2>&1; then
  if curl -fsSL "${DOWNLOAD_URL}" -o "${INSTALL_DIR}/aayi" 2>/dev/null; then
    DOWNLOAD_SUCCESS=1
  fi
elif command -v wget >/dev/null 2>&1; then
  if wget -qO "${INSTALL_DIR}/aayi" "${DOWNLOAD_URL}" 2>/dev/null; then
    DOWNLOAD_SUCCESS=1
  fi
fi

if [ "${DOWNLOAD_SUCCESS}" -eq 0 ]; then
  echo "Standalone binary not yet released. Falling back to source installation..."
  if command -v uv >/dev/null 2>&1; then
    echo "Installing via uv tool..."
    uv tool install --force "git+https://github.com/${REPO}.git"
    DOWNLOAD_SUCCESS=1
  elif command -v pipx >/dev/null 2>&1; then
    echo "Installing via pipx..."
    pipx install --force "git+https://github.com/${REPO}.git"
    DOWNLOAD_SUCCESS=1
  elif command -v python3 >/dev/null 2>&1; then
    echo "Installing via isolated Python environment..."
    VENV_DIR="${HOME}/.local/share/aayi/venv"
    mkdir -p "${HOME}/.local/share/aayi"
    python3 -m venv "${VENV_DIR}"
    "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
    "${VENV_DIR}/bin/pip" install --quiet "git+https://github.com/${REPO}.git"
    ln -sf "${VENV_DIR}/bin/aayi" "${INSTALL_DIR}/aayi"
    DOWNLOAD_SUCCESS=1
  else
    echo "Error: Release binary not found and neither uv, pipx, nor python3 was detected."
    exit 1
  fi
fi

if [ -f "${INSTALL_DIR}/aayi" ]; then
  chmod +x "${INSTALL_DIR}/aayi"
fi

echo "Aayi installed successfully!"

# Check if INSTALL_DIR is in PATH
case ":${PATH}:" in
  *":${INSTALL_DIR}:"*) ;;
  *)
    echo ""
    echo "Note: ${INSTALL_DIR} is not currently in your PATH."
    echo "To add it, append this to your ~/.bashrc or ~/.zshrc:"
    echo "  export PATH=\"${INSTALL_DIR}:\$PATH\""
    ;;
esac

echo ""
echo "Run 'aayi --help' to get started!"
