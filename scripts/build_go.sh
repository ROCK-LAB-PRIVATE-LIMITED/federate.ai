#!/bin/bash
# Build Go sidecars locally (for development or source installs)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
GO_DIR="$PROJECT_ROOT/go"
BIN_DIR="$PROJECT_ROOT/src/federate/bin"

echo "==================================================="
echo "Federate Go Sidecars Build Script"
echo "==================================================="

mkdir -p "$BIN_DIR"

# Detect platform suffix
if [ "$(uname)" = "Darwin" ]; then
    SUFFIX=""
elif [ "$(uname)" = "Linux" ]; then
    SUFFIX=""
else
    SUFFIX=".exe"
fi

export GOWORK=off

cd "$GO_DIR"

echo "[1/3] Building federate_bridge..."
go build -o "$BIN_DIR/federate_bridge${SUFFIX}" ./cmd/bridge

echo "[2/3] Building federate_embed..."
go build -o "$BIN_DIR/federate_embed${SUFFIX}" ./cmd/embed

echo "[3/3] Building federate_search..."
go build -o "$BIN_DIR/federate_search${SUFFIX}" ./cmd/search

echo "==================================================="
echo "Build complete! Binaries placed in: $BIN_DIR"
ls -la "$BIN_DIR"/federate_*
echo "==================================================="
