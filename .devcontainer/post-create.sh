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
  shellcheck \
  yamllint \
  build-essential
sudo apt-get clean
sudo rm -rf /var/lib/apt/lists/*

# 2. Install Python
echo "==> Ensuring Python 3.14 via uv..."
uv python install --default 3.14

# 3. Install CLI tools via uv tool
echo "==> Installing ty static type checker via uv tool..."
uv tool install --upgrade ty

# 4. Install just command runner
echo "==> Installing just command runner..."
if ! command -v just > /dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | bash -s -- --to "${HOME}/.local/bin"
else
  echo "    just already installed ($(just --version))."
fi

echo "==> Installing shell completions for just..."
sudo install -d -m 0755 /usr/share/bash-completion/completions /usr/share/zsh/vendor-completions
just --completions bash | sudo tee /usr/share/bash-completion/completions/just > /dev/null || true
just --completions zsh | sudo tee /usr/share/zsh/vendor-completions/_just > /dev/null || true

# 5. Set up Python virtual environment and install Eidos in editable mode
echo "==> Setting up Eidos virtual environment and dependencies..."
uv venv .venv
uv pip install -e ".[dev]"

# 6. Configure persistent AI tool directories and migrate initial backups
echo "==> Configuring persistent volumes (~/.gemini, ~/.claude)..."
sudo mkdir -p "${HOME}/.gemini" "${HOME}/.claude"
sudo chown -R vscode:vscode "${HOME}/.gemini" "${HOME}/.claude"
chmod 700 "${HOME}/.gemini" "${HOME}/.claude"

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

if [ -d "${BACKUP_DIR}/.claude" ]; then
  echo "    Migrating initial Claude data from ${BACKUP_DIR}/.claude..."
  cp -a "${BACKUP_DIR}/.claude/." "${HOME}/.claude/"
  rm -rf "${BACKUP_DIR}/.claude"
elif [ -d "${BACKUP_DIR}/claude" ]; then
  echo "    Migrating initial Claude data from ${BACKUP_DIR}/claude..."
  cp -a "${BACKUP_DIR}/claude/." "${HOME}/.claude/"
  rm -rf "${BACKUP_DIR}/claude"
fi

if [ -d "${BACKUP_DIR}" ]; then
  rmdir "${BACKUP_DIR}" 2>/dev/null || true
fi

sudo chown -R vscode:vscode "${HOME}/.gemini" "${HOME}/.claude"

# 7. Validate Eidos specifications
if [ -f ".venv/bin/eidos" ]; then
  echo "==> Validating Eidos living specifications..."
  .venv/bin/eidos validate eidos || true
fi

echo "========================================================"
echo "✅ Eidos Dev Container Ready!"
echo "   - uv:         $(uv --version)"
echo "   - ty:         $(ty --version 2>/dev/null || echo 'installed')"
echo "   - python:     $(python --version 2>/dev/null || echo 'not on PATH')"
echo "   - just:       $(just --version 2>/dev/null || echo 'installed')"
echo "   - shellcheck: $(shellcheck --version | grep version: || echo 'installed')"
echo "   - yamllint:   $(yamllint --version || echo 'installed')"
echo "========================================================"
