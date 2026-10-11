#!/usr/bin/env bash
set -euo pipefail

echo "========================================================"
echo "🚀 Initializing Eidos Dev Container Environment..."
echo "========================================================"

export PATH="$HOME/.local/bin:$PATH"

# 1. Install Python 3.15 via uv
echo "==> Ensuring Python 3.15 via uv..."
uv python install --default 3.15

# 2. Set up Python virtual environment and install Eidos in editable mode
echo "==> Setting up Eidos virtual environment with Python 3.15..."
uv venv --python 3.15 .venv
uv pip install -e ".[dev,docs]"

# 3. Configure persistent AI tool directories and migrate initial backups
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

# 4. Validate Eidos specifications
if [ -f ".venv/bin/eidos" ]; then
  echo "==> Validating Eidos living specifications..."
  .venv/bin/eidos validate eidos || true
fi

echo "========================================================"
echo "✅ Eidos Dev Container Ready!"
echo "   - uv:         $(uv --version)"
echo "   - python:     $(python --version 2>/dev/null || echo 'not on PATH')"
echo "========================================================"
