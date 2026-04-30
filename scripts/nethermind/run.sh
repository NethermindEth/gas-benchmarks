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
    cat > "$SCRIPT_DIR/docker-compose.override.yml" <<OVERRIDE
services:
  execution:
    environment:
      - DIAG_WITH=${DIAG_WITH}
OVERRIDE
    echo "[diag] Created override with DIAG_WITH=$DIAG_WITH"
fi

# After compose pull, inspect the image entrypoint
IMAGE=$(grep '^EC_IMAGE_VERSION=' "$SCRIPT_DIR/.env" | cut -d= -f2-)
if [ -n "$IMAGE" ]; then
    docker pull "$IMAGE" >/dev/null 2>&1 || true
    echo "[diag] Image: $IMAGE"
    echo "[diag] Entrypoint: $(docker inspect "$IMAGE" --format '{{json .Config.Entrypoint}}' 2>/dev/null || echo 'N/A')"
    echo "[diag] Cmd: $(docker inspect "$IMAGE" --format '{{json .Config.Cmd}}' 2>/dev/null || echo 'N/A')"
    echo "[diag] Has entrypoint.sh: $(docker run --rm --entrypoint='' "$IMAGE" ls -la ./entrypoint.sh 2>&1 || echo 'NOT FOUND')"
fi

pushd "$SCRIPT_DIR" >/dev/null
echo "[diag] Merged compose config (environment):"
compose_cmd config 2>/dev/null | grep -A10 'environment:' | head -12 || true
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
