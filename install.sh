#!/bin/bash
# ==============================================================================
#            Federate.AI Unified Cross-Platform Installer Script
# ==============================================================================
# Supported Platforms:
# - macOS (Intel x86_64 & Apple Silicon ARM64)
# - Linux (x86_64 & ARM64 / aarch64)
# - Android (Termux environment)
# - Windows (WSL & native environments via Git Bash/MSYS/Cygwin)
# ==============================================================================
set -e

echo "======================================================================"
echo "          Federate.AI Universal uv-Based Installer Bootstrapper        "
echo "======================================================================"

# 1. Platform and Shell Detection
OS_NAME="$(uname -s)"
ARCH_NAME="$(uname -m)"
IS_TERMUX=false
IS_WINDOWS_BASH=false
IS_ARM=false

# Detect Termux (Android)
if [ -d "/data/data/com.termux" ]; then
    IS_TERMUX=true
fi

# Detect Windows Bash Environment (Git Bash, MSYS, Cygwin)
case "$OS_NAME" in
    *MINGW*|*MSYS*|*CYGWIN*)
        IS_WINDOWS_BASH=true
        ;;
esac

# Detect ARM Architecture (for Linux ARM checks)
case "$ARCH_NAME" in
    arm*|aarch64*|arm64*)
        IS_ARM=true
        ;;
esac

echo "[*] System Architecture: $ARCH_NAME"

# 2. Windows Delegation Routing
if [ "$IS_WINDOWS_BASH" = true ]; then
    echo "[*] Windows Bash environment detected (Git Bash / MSYS / Cygwin)."
    echo "[*] Transitioning execution context to native Windows PowerShell..."
    
    # Hand off execution to native Windows PowerShell to bootstrap native Windows binaries on Python 3.12
    powershell.exe -ExecutionPolicy Bypass -Command "
        Write-Host '[*] Bootstrapping uv on Windows via PowerShell...' -ForegroundColor Cyan
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            irm https://astral.sh/uv/install.ps1 | iex
            \$env:PATH = [System.Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [System.Environment]::GetEnvironmentVariable('Path', 'Machine')
        }
        Write-Host '[*] Installing Federate.AI on standardized Python 3.12 environment...' -ForegroundColor Cyan
        uv tool install --python 3.12 'federate[all]'
    "
    echo "======================================================================"
    echo " 🎉 Windows installation complete!"
    echo " Please restart your terminal/shell to ensure paths are updated."
    echo "======================================================================"
    exit 0
fi

# 3. Unix-Based Setup (Linux, macOS, Termux, WSL)
if [ "$IS_TERMUX" = true ]; then
    echo "[*] Platform: Android (Termux)"
elif [ "$OS_NAME" = "Darwin" ]; then
    echo "[*] Platform: macOS ($OS_NAME)"
elif [ "$OS_NAME" = "Linux" ]; then
    # Check if running under WSL
    if grep -qE "(Microsoft|Microsoft|wsl)" /proc/version 2>/dev/null; then
        echo "[*] Platform: Linux (WSL)"
    else
        echo "[*] Platform: Linux (Native)"
    fi
else
    echo "[*] Platform: POSIX-compliant Unix ($OS_NAME)"
fi

# 4. Ensure uv is Installed on Unix-like Systems
ensure_uv_unix() {
    if command -v uv &> /dev/null; then
        echo "[*] uv is already available on your PATH."
        return 0
    fi

    echo "[*] uv not detected. Commencing installer..."

    if [ "$IS_TERMUX" = true ]; then
        echo "[*] Installing native Termux package for uv..."
        pkg update -y
        pkg install -y uv
    else
        echo "[*] Running official Astral standalone uv installer..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
    fi

    # Append potential install directories to PATH for immediate use
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    if ! command -v uv &> /dev/null; then
        echo "[!] uv installation could not be verified automatically."
        echo "[*] Please install uv manually (https://docs.astral.sh/uv/) and run:"
        echo "    uv tool install federate[all]"
        return 1
    fi

    echo "[*] uv successfully configured."
}

# 5. Perform Unix-Based Installation using uv tool
install_federate_unix() {
    # GitHub repository config parameters for pre-compiled "Tyre" wheels
    REPO_OWNER="ROCK-LAB-PRIVATE-LIMITED"  # <-- CHANGE THIS to your GitHub organization/username
    REPO_NAME="federate.ai"       # <-- CHANGE THIS to your repository name
    BRANCH="main"

    TYRES_DIR="/tmp/federate_tyres"
    rm -rf "$TYRES_DIR" && mkdir -p "$TYRES_DIR"
    RAW_URL="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${BRANCH}/tyres"

    if [ "$IS_TERMUX" = true ]; then
        echo "[*] Android (Termux) environment detected."
        echo "[*] Downloading precompiled Python 3.12 wheels from tyres folder..."

        WHEELS=(
            "numpy-2.4.4-cp312-cp312-android_24_arm64_v8a.whl"
        )

        DOWNLOAD_SUCCESS=false
        for wheel in "${WHEELS[@]}"; do
            echo "    [*] Downloading: $wheel"
            if curl -LsSf "$RAW_URL/$wheel" -o "$TYRES_DIR/$wheel"; then
                echo "        [+] Success: Local binary cached."
                DOWNLOAD_SUCCESS=true
            else
                echo "        [!] Warning: Precompiled binary not found."
            fi
        done

        if [ "$DOWNLOAD_SUCCESS" = true ]; then
            echo "[*] Installing Federate with full extras [all] using pre-compiled wheels on Python 3.12..."
            uv tool install --python 3.12 --find-links "$TYRES_DIR" "federate[all]"
        else
            echo "[!] Pre-compiled wheels not found."
            echo "[!] Falling back to basic installation (no extras) to prevent compilation hangs."
            uv tool install --python 3.12 federate
        fi

    elif [ "$OS_NAME" = "Linux" ] && [ "$IS_ARM" = true ]; then
        echo "[*] Linux ARM64 (aarch64) environment detected."
        echo "[*] Downloading precompiled Python 3.12 wheels from tyres folder..."

        WHEELS=(
            "numpy-2.4.4-cp312-cp312-manylinux2014_aarch64.whl"
        )

        DOWNLOAD_SUCCESS=false
        for wheel in "${WHEELS[@]}"; do
            echo "    [*] Downloading: $wheel"
            if curl -LsSf "$RAW_URL/$wheel" -o "$TYRES_DIR/$wheel"; then
                echo "        [+] Success: Local binary cached."
                DOWNLOAD_SUCCESS=true
            else
                echo "        [!] Warning: Precompiled binary not found."
            fi
        done

        if [ "$DOWNLOAD_SUCCESS" = true ]; then
            echo "[*] Installing Federate with full extras [all] using pre-compiled wheels on Python 3.12..."
            uv tool install --python 3.12 --find-links "$TYRES_DIR" "federate[all]"
        else
            echo "[!] Pre-compiled wheels not found."
            echo "[!] Falling back to basic installation (no extras) to prevent compilation hangs."
            uv tool install --python 3.12 federate
        fi

    else
        # macOS, WSL, or standard Linux (x86_64) - Standardized on Python 3.12
        echo "[*] Desktop/Server environment detected."
        echo "[*] Installing Federate with all features on standardized Python 3.12 environment..."
        uv tool install --python 3.12 "federate[all]"
    fi

    # Clean up cached binaries
    rm -rf "$TYRES_DIR"
}

# Execute Unix sequence
if ensure_uv_unix; then
    install_federate_unix
    echo "======================================================================"
    echo " 🎉 Installation finished successfully!"
    echo "======================================================================"
    echo " To ensure both 'uv' and 'federate' are on your shell's PATH, "
    echo " please restart your terminal or run:"
    echo "     source \$HOME/.local/bin/env"
    echo "     source ~/.bashrc  (or ~/.zshrc if using Zsh)"
    echo ""
    echo " To run the application:"
    echo "     federate"
    echo "======================================================================"
else
    echo "[!] Installation could not be completed."
    exit 1
fi