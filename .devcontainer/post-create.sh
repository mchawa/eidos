#!/usr/bin/env bash
set -euo pipefail

echo "========================================================"
echo "🚀 Initializing Eidos Dev Container Environment..."
echo "========================================================"

export PATH="$HOME/.local/bin:$PATH"

# 1. Install base utilities from Ubuntu archive
echo "==> Installing base toolchain from Ubuntu archive..."
sudo DEBIAN_FRONTEND=noninteractive apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  gettext-base \
  openssl \
  iputils-ping \
  dnsutils \
  yamllint \
  build-essential
sudo apt-get clean
sudo rm -rf /var/lib/apt/lists/*

# 2. Install Python 3.15 via uv
echo "==> Ensuring Python 3.15 via uv..."
uv python install --default 3.15

# 3. Install CLI tools via uv tool
echo "==> Installing ty static type checker via uv tool..."
uv tool install --upgrade ty

# 4. Set up Python virtual environment and install Eidos in editable mode
echo "==> Setting up Eidos virtual environment with Python 3.15..."
uv venv --python 3.15 .venv
uv pip install -e ".[dev]"

# 5. Configure persistent AI tool directories and migrate initial backups
echo "==> Configuring persistent volumes (~/.gemini)..."
sudo mkdir -p "${HOME}/.gemini"
sudo chown -R vscode:vscode "${HOME}/.gemini"
chmod 700 "${HOME}/.gemini"

BACKUP_DIR="${PWD}/backups"
if [ -d "${BACKUP_DIR}/.gemini" ]; then
  echo "    Migrating initial Gemini data from ${BACKUP_DIR}/.gemini..."
  cp -a "${BACKUP_DIR}/.gemini/." "${HOME}/.gemini/"
  rm -rf "${BACKUP_DIR}/.gemini"
elif [ -d "${BACKUP_DIR}/gemini" ]; then
  echo "    Migrating initial Gemini data from ${BACKUP_DIR}/gemini..."
  cp -a "${BACKUP_DIR}/gemini/." "${HOME}/.gemini/"
  rm -rf "${BACKUP_DIR}/gemini"
fi

if [ -d "${BACKUP_DIR}" ]; then
  rmdir "${BACKUP_DIR}" 2>/dev/null || true
fi

sudo chown -R vscode:vscode "${HOME}/.gemini"

# 6. Validate Eidos specifications
if [ -f ".venv/bin/eidos" ]; then
  echo "==> Validating Eidos living specifications..."
  .venv/bin/eidos validate eidos || true
fi

echo "========================================================"
echo "✅ Eidos Dev Container Ready!"
echo "   - uv:         $(uv --version)"
echo "   - ty:         $(ty --version 2>/dev/null || echo 'installed')"
echo "   - python:     $(python --version 2>/dev/null || echo 'not on PATH')"
echo "   - yamllint:   $(yamllint --version || echo 'installed')"
echo "========================================================"
