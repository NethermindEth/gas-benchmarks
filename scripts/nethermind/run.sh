#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cp "$SCRIPT_DIR/jwtsecret" /tmp/jwtsecret

# shellcheck source=/dev/null
source "$REPO_ROOT/scripts/common/wait_for_rpc.sh"
# shellcheck source=/dev/null
source "$REPO_ROOT/scripts/common/docker_compose.sh"

rm -f "$SCRIPT_DIR/docker-compose.override.yml"

if [ -n "${DIAG_WITH:-}" ]; then
    DIAG_SCRIPT="$SCRIPT_DIR/diag-entrypoint.sh"
    chmod +x "$DIAG_SCRIPT"
    ABS_DIAG_SCRIPT="$(cd "$(dirname "$DIAG_SCRIPT")" && pwd)/$(basename "$DIAG_SCRIPT")"

    cat > "$SCRIPT_DIR/docker-compose.override.yml" <<OVERRIDE
services:
  execution:
    environment:
      - DIAG_WITH=${DIAG_WITH}
    entrypoint: ["./diag-entrypoint.sh"]
    volumes:
      - ${ABS_DIAG_SCRIPT}:/nethermind/diag-entrypoint.sh:ro
OVERRIDE
    echo "[diag] Override: mount diag-entrypoint.sh + set DIAG_WITH=$DIAG_WITH"
    cat "$SCRIPT_DIR/docker-compose.override.yml"
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
