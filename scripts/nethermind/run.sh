#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cp "$SCRIPT_DIR/jwtsecret" /tmp/jwtsecret

# shellcheck source=/dev/null
source "$REPO_ROOT/scripts/common/wait_for_rpc.sh"

pushd "$SCRIPT_DIR" >/dev/null
docker compose up -d
popd >/dev/null

echo "Invoking wait_for_rpc for Nethermind RPC readiness..."
if ! wait_for_rpc "http://0.0.0.0:8545" 50; then
    echo "RPC failed to start. Dumping logs..."
    pushd "$SCRIPT_DIR" >/dev/null
    docker compose logs
    popd >/dev/null
    exit 1
fi

pushd "$SCRIPT_DIR" >/dev/null
docker compose logs
popd >/dev/null
