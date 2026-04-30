#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cp "$SCRIPT_DIR/jwtsecret" /tmp/jwtsecret

# shellcheck source=/dev/null
source "$REPO_ROOT/scripts/common/wait_for_rpc.sh"
# shellcheck source=/dev/null
source "$REPO_ROOT/scripts/common/docker_compose.sh"

echo "[diag] SCRIPT_DIR=$SCRIPT_DIR"
echo "[diag] .env path: $SCRIPT_DIR/.env"
echo "[diag] .env BEFORE append:"
cat "$SCRIPT_DIR/.env" || echo "(cat failed)"
echo "[diag] DIAG_WITH from env: '${DIAG_WITH:-}'"
if [ -n "${DIAG_WITH:-}" ]; then
    printf '\n%s\n' "DIAG_WITH=$DIAG_WITH" >> "$SCRIPT_DIR/.env"
    echo "[diag] .env AFTER append:"
    cat "$SCRIPT_DIR/.env"
fi

pushd "$SCRIPT_DIR" >/dev/null
compose_cmd up --detach
popd >/dev/null

echo "Invoking wait_for_rpc for Nethermind RPC readiness..."
if ! wait_for_rpc "http://0.0.0.0:8545" 300; then
    echo "RPC failed to start. Dumping logs..."
    # Change into the script directory to ensure compose logs run in the correct context.
    pushd "$SCRIPT_DIR" >/dev/null
    compose_cmd logs
    popd >/dev/null
    # No sense in continuing if the RPC is not ready
    exit 1
fi

pushd "$SCRIPT_DIR" >/dev/null
compose_cmd logs
popd >/dev/null
