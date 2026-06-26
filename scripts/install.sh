#!/usr/bin/env bash
# Install dependencies for the Pokemon Agent skill.
# Works on macOS, Linux, and inside stereOS VMs (NixOS).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Pokemon Agent Setup ==="
echo "Skill directory: $SKILL_DIR"

# ---------------------------------------------------------------------------
# Detect NixOS (stereOS VMs use NixOS)
# ---------------------------------------------------------------------------
IS_NIXOS=false
if [ -f /etc/NIXOS ] || [ -d /nix/store ]; then
    IS_NIXOS=true
    echo "Detected NixOS environment"
fi

# ---------------------------------------------------------------------------
# Fix DNS inside stereOS VMs (systemd-resolved stub often broken)
# ---------------------------------------------------------------------------
if $IS_NIXOS; then
    if ! nslookup google.com &>/dev/null 2>&1; then
        echo "Fixing DNS (systemd-resolved not forwarding)..."
        sudo bash -c 'echo "nameserver 8.8.8.8" > /etc/resolv.conf'
    fi
fi

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------
if ! command -v python3 &>/dev/null; then
    if $IS_NIXOS; then
        echo "Installing Python via nix..."
        nix profile install nixpkgs#python312
    else
        echo "ERROR: python3 not found. Install Python 3.10+ first."
        exit 1
    fi
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Python version: $PYTHON_VERSION"

# ---------------------------------------------------------------------------
# PyBoy + deps (NixOS needs a venv + native libs)
# ---------------------------------------------------------------------------
if $IS_NIXOS; then
    # Native libraries PyBoy/numpy need
    for pkg in gcc-unwrapped.lib zlib; do
        if ! nix profile list 2>/dev/null | grep -q "$pkg"; then
            echo "Installing nix package: $pkg"
            nix profile install "nixpkgs#$pkg"
        fi
    done
    export LD_LIBRARY_PATH="$HOME/.nix-profile/lib:${LD_LIBRARY_PATH:-}"

    # Use a venv so pip doesn't try to write to /nix/store
    if [ ! -d "$HOME/venv" ]; then
        echo "Creating Python venv..."
        python3 -m venv "$HOME/venv"
    fi
    echo "Installing PyBoy into venv..."
    "$HOME/venv/bin/pip" install --quiet pyboy Pillow numpy
    echo "Verifying PyBoy..."
    "$HOME/venv/bin/python3" -c "from pyboy import PyBoy; print('PyBoy OK')"
else
    echo ""
    echo "Installing PyBoy and dependencies..."
    pip3 install --quiet --break-system-packages pyboy Pillow numpy 2>/dev/null \
        || pip3 install --quiet pyboy Pillow numpy
    echo "Verifying PyBoy..."
    python3 -c "from pyboy import PyBoy; print('PyBoy OK')"
fi

# ---------------------------------------------------------------------------
# Writable directories (shared mount permissions)
# ---------------------------------------------------------------------------
# The stereOS shared mount preserves host file ownership (UID 501 on macOS).
# The VM runs as admin (UID 1000), so host-created directories are read-only
# unless we open permissions. These directories hold runtime output that the
# agent writes during a session.
for dir in frames pokedex; do
    mkdir -p "$SKILL_DIR/$dir"
    chmod a+rwx "$SKILL_DIR/$dir" 2>/dev/null || true
done

# ---------------------------------------------------------------------------
# Paper (paperd proxy)
# ---------------------------------------------------------------------------
if [ -z "${ANTHROPIC_BASE_URL:-}" ]; then
    echo "WARNING: ANTHROPIC_BASE_URL is not set."
    echo "Run 'paper init' on the host and ensure paperd is running before starting agents."
else
    echo "Paper proxy: $ANTHROPIC_BASE_URL"
fi

echo ""
echo "=== Setup complete ==="
