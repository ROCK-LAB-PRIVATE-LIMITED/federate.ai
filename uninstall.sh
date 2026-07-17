#!/bin/bash
# ==============================================================================
#            Federate.AI Unified Cross-Platform Uninstaller Script
# ==============================================================================
# Supported Platforms:
# - macOS (Intel & Apple Silicon)
# - Linux (x86_64 & ARM)
# - Android (Termux environment)
# - Windows (WSL & native environments via Git Bash/MSYS/Cygwin)
# ==============================================================================
set -e

echo "======================================================================"
echo "          Federate.AI Universal uv-Based Uninstaller                   "
echo "======================================================================"

# 1. Platform and Shell Detection
OS_NAME="$(uname -s)"
IS_TERMUX=false
IS_WINDOWS_BASH=false

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

# Ensure standard uv local bin paths are on the PATH for the uninstaller
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# 2. Windows Delegation Routing
if [ "$IS_WINDOWS_BASH" = true ]; then
    echo "[*] Windows Bash environment detected."
    echo "[*] Transitioning execution context to native Windows PowerShell..."
    
    # Delegate to PowerShell to remove only the executable environment
    powershell.exe -ExecutionPolicy Bypass -Command "
        Write-Host '[*] Uninstalling Federate executable via uv...' -ForegroundColor Cyan
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            uv tool uninstall federate
        } else {
            Write-Host '[!] uv command not found. Performing manual tool environment purge...' -ForegroundColor Yellow
            \$uvToolPath = Join-Path \$env:USERPROFILE 'AppData\Roaming\uv\tools\federate'
            \$uvBinPath = Join-Path \$env:USERPROFILE '.local\bin\federate.exe'
            if (Test-Path \$uvToolPath) { Remove-Item -Recurse -Force \$uvToolPath }
            if (Test-Path \$uvBinPath) { Remove-Item -Force \$uvBinPath }
        }
    "
    echo "======================================================================"
    echo " 🎉 Windows uninstallation complete!"
    echo " (Persistent configurations and workspaces have been preserved.)"
    echo "======================================================================"
    exit 0
fi

# 3. Unix-Based Uninstallation (Linux, macOS, Termux, WSL)
# Remove only the uv-managed tool environment and binaries
if command -v uv &> /dev/null; then
    echo "[*] Removing Federate executable and virtual environments via uv..."
    uv tool uninstall federate || true
else
    echo "[!] 'uv' command not found on PATH."
    echo "[*] Performing direct filesystem purge of the isolated tool environment..."
    
    # Direct filesystem fallback: Purge standard tool and symlink directories
    # to guarantee uninstallation even if uv was deleted or path is broken
    rm -f "$HOME/.local/bin/federate" || true
    
    # Remove from standard Linux/Termux and macOS uv directories
    rm -rf "$HOME/.local/share/uv/tools/federate" || true
    rm -rf "$HOME/Library/Application Support/uv/tools/federate" || true
fi

echo "======================================================================"
echo " 🎉 Federate.AI has been successfully uninstalled."
echo " Note: Your local configuration databases, models, and workspaces "
echo " in ~/.federate and your workspace folders have been preserved."
echo "======================================================================"