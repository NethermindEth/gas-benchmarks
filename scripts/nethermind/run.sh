#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cp "$SCRIPT_DIR/jwtsecret" /tmp/jwtsecret

# shellcheck source=/dev/null
source "$REPO_ROOT/scripts/common/wait_for_rpc.sh"
# shellcheck source=/dev/null
source "$REPO_ROOT/scripts/common/docker_compose.sh"

if [ -n "${DIAG_WITH:-}" ]; then
    echo "[diag] Injecting DIAG_WITH=$DIAG_WITH into docker-compose.yaml"
    sed -i "s|COLORTERM=truecolor|COLORTERM=truecolor\n      - DIAG_WITH=${DIAG_WITH}|" "$SCRIPT_DIR/docker-compose.yaml"
    echo "[diag] environment section after injection:"
    grep -A3 'environment:' "$SCRIPT_DIR/docker-compose.yaml" | head -5
fi

pushd "$SCRIPT_DIR" >/dev/null
compose_cmd up --detach
popd >/dev/null

echo "Invoking wait_for_rpc for Nethermind RPC readiness..."
if ! wait_for_rpc "http://0.0.0.0:8545" 300; then
    echo "RPC failed to start. Dumping logs..."
    pushd "$SCRIPT_DIR" >/dev/null
    compose_cmd logs
    popd >/dev/null
    exit 1
fi

pushd "$SCRIPT_DIR" >/dev/null
compose_cmd logs
popd >/dev/null
