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
wait_for_rpc "http://127.0.0.1:8545" 300

pushd "$SCRIPT_DIR" >/dev/null
docker compose logs
popd >/dev/null
