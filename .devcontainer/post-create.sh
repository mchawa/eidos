#!/usr/bin/env bash
set -euo pipefail

echo "========================================================"
echo "🚀 Initializing Eidos Dev Container Environment..."
echo "========================================================"

export PATH="$HOME/.local/bin:$PATH"

# 1. Install Python 3.15 via uv
echo "==> Ensuring Python 3.15 via uv..."
uv python install --default 3.15

# 2. Set up Python virtual environment and install Eidos CLI tool
echo "==> Setting up Eidos environment with Python 3.15..."
uv sync --all-extras --dev
uv tool install --force --editable .

# 3. Install Living Portal frontend dependencies
if [ -d "src/eidos/portal/frontend" ] && command -v pnpm &>/dev/null; then
  echo "==> Installing Living Portal frontend dependencies via pnpm..."
  (cd src/eidos/portal/frontend && pnpm install)
fi

# 4. Configure persistent AI tool directories
echo "==> Configuring persistent volumes (~/.gemini)..."
sudo mkdir -p "${HOME}/.gemini"
sudo chown -R vscode:vscode "${HOME}/.gemini"
chmod 700 "${HOME}/.gemini"

# 5. Validate Eidos specifications
if [ -f ".venv/bin/eidos" ]; then
  echo "==> Validating Eidos living specifications..."
  .venv/bin/eidos validate eidos || true
fi

echo "========================================================"
echo "✅ Eidos Dev Container Ready!"
echo "   - uv:         $(uv --version)"
echo "   - python:     $(python --version 2>/dev/null || echo 'not on PATH')"
echo "   - node:       $(node --version 2>/dev/null || echo 'not on PATH')"
echo "   - pnpm:       $(pnpm --version 2>/dev/null || echo 'not on PATH')"
echo "========================================================"
