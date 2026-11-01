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

if command -v curl >/dev/null 2>&1; then
  curl -fsSL "${DOWNLOAD_URL}" -o "${INSTALL_DIR}/aayi"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "${INSTALL_DIR}/aayi" "${DOWNLOAD_URL}"
else
  echo "Error: Neither curl nor wget was found. Please install curl or wget first."
  exit 1
fi

chmod +x "${INSTALL_DIR}/aayi"

echo "Aayi installed successfully to ${INSTALL_DIR}/aayi"

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
