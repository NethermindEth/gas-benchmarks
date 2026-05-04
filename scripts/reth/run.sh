#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cp "$SCRIPT_DIR/jwtsecret" /tmp/jwtsecret

# shellcheck source=/dev/null
source "$REPO_ROOT/scripts/common/wait_for_rpc.sh"
# shellcheck source=/dev/null
source "$REPO_ROOT/scripts/common/docker_compose.sh"

pushd "$SCRIPT_DIR" >/dev/null
compose_cmd up --detach
popd >/dev/null

wait_for_rpc "http://127.0.0.1:8545"

pushd "$SCRIPT_DIR" >/dev/null
compose_cmd logs
popd >/dev/null
