#!/usr/bin/env bash
set -euo pipefail

VERSION="${VERSION:-"latest"}"
SEMANTIC="${SEMANTIC:-"false"}"
PYTHON_VERSION="${PYTHONVERSION:-"3.14"}"

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Script must be run as root. Use sudo, su, or add 'USER root' to your Dockerfile before running this script." >&2
    exit 1
fi

install_system_packages() {
    local pkgs=("$@")
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -y
        apt-get install -y --no-install-recommends "${pkgs[@]}"
        rm -rf /var/lib/apt/lists/*
    elif command -v apk >/dev/null 2>&1; then
        apk add --no-cache "${pkgs[@]}"
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y "${pkgs[@]}"
        dnf clean all
    elif command -v microdnf >/dev/null 2>&1; then
        microdnf install -y "${pkgs[@]}"
        microdnf clean all
    elif command -v yum >/dev/null 2>&1; then
        yum install -y "${pkgs[@]}"
        yum clean all
    else
        echo "ERROR: No supported package manager (apt-get, apk, dnf, microdnf, yum) found to install ${pkgs[*]}." >&2
        exit 1
    fi
}

# 1. Ensure curl, ca-certificates, tar, and git are present
MISSING_PKGS=()
command -v curl >/dev/null 2>&1 || MISSING_PKGS+=("curl")
command -v git >/dev/null 2>&1 || MISSING_PKGS+=("git")
command -v tar >/dev/null 2>&1 || MISSING_PKGS+=("tar")
if [ ! -f /etc/ssl/certs/ca-certificates.crt ] && [ ! -f /etc/pki/tls/certs/ca-bundle.crt ] && [ ! -f /etc/ssl/cert.pem ]; then
    MISSING_PKGS+=("ca-certificates")
fi

if [ "${#MISSING_PKGS[@]}" -gt 0 ]; then
    echo "==> Installing missing system prerequisites (${MISSING_PKGS[*]})..."
    install_system_packages "${MISSING_PKGS[@]}"
fi

# 2. Isolate uv cache in a temporary directory that is purged on exit to keep OCI layers minimal
UV_CACHE_DIR="$(mktemp -d)"
export UV_CACHE_DIR
trap 'rm -rf "${UV_CACHE_DIR}"' EXIT

# 3. Ensure uv is available on PATH
export PATH="/usr/local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    echo "==> Installing uv to /usr/local/bin..."
    env UV_INSTALL_DIR="/usr/local/bin" INSTALLER_NO_MODIFY_PATH=1 sh -c "$(curl -fsSL https://astral.sh/uv/install.sh)"
    chmod a+rx /usr/local/bin/uv /usr/local/bin/uvx 2>/dev/null || true
fi

# 4. Validate Python version constraint for optional [semantic] ONNX Runtime extra (requires Python 3.11-3.14)
if [ "${SEMANTIC}" = "true" ]; then
    case "${PYTHON_VERSION}" in
        3.11*|3.12*|3.13*|3.14*)
            ;;
        *)
            echo "==> Note: eidos-ai[semantic] requires Python 3.11-3.14 for prebuilt onnxruntime wheels (requested '${PYTHON_VERSION}'); clamping to 3.14."
            PYTHON_VERSION="3.14"
            ;;
    esac
fi

# 5. Construct PyPI package specifier (strip optional leading 'v' from version tags like 'v0.5.0')
PKG="eidos-ai"
if [ "${SEMANTIC}" = "true" ]; then
    PKG="eidos-ai[semantic]"
fi
CLEAN_VERSION="${VERSION#v}"
if [ -n "${CLEAN_VERSION}" ] && [ "${CLEAN_VERSION}" != "latest" ]; then
    PKG="${PKG}==${CLEAN_VERSION}"
fi

# 6. Provision Python and install Eidos CLI system-wide via uv without caching wheels in the image layer
export UV_TOOL_BIN_DIR="/usr/local/bin"
export UV_TOOL_DIR="/usr/local/share/uv/tools"
export UV_PYTHON_INSTALL_DIR="/usr/local/share/uv/python"

mkdir -p "${UV_TOOL_BIN_DIR}" "${UV_TOOL_DIR}" "${UV_PYTHON_INSTALL_DIR}"

echo "==> Installing Python ${PYTHON_VERSION} into ${UV_PYTHON_INSTALL_DIR}..."
uv python install "${PYTHON_VERSION}"

echo "==> Installing ${PKG} (Python ${PYTHON_VERSION}) into ${UV_TOOL_DIR}..."
uv tool install --no-cache --force --python "${PYTHON_VERSION}" "${PKG}"

chmod -R a+rX /usr/local/share/uv /usr/local/bin/eidos

echo "✅ Eidos Dev Container Feature installed: $(/usr/local/bin/eidos --version)"

