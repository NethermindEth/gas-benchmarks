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

if [ -f "$SCRIPT_DIR/.env" ]; then
    EXTRA_CLIENT_FLAGS="$(grep -oP '^EXTRA_CLIENT_FLAGS=\K.*' "$SCRIPT_DIR/.env" || true)"
fi

NEED_OVERRIDE=false
OVERRIDE_ENV_LINES=""
OVERRIDE_ENTRYPOINT=""
OVERRIDE_VOLUMES=""

append_override_volume() {
    local volume="$1"

    if [ -n "$OVERRIDE_VOLUMES" ]; then
        OVERRIDE_VOLUMES="${OVERRIDE_VOLUMES}
${volume}"
    else
        OVERRIDE_VOLUMES="${volume}"
    fi
}

if [ -n "${DIAG_WITH:-}" ]; then
    NEED_OVERRIDE=true
    DIAG_SCRIPT="$SCRIPT_DIR/diag-entrypoint.sh"
    chmod +x "$DIAG_SCRIPT"
    ABS_DIAG_SCRIPT="$(cd "$(dirname "$DIAG_SCRIPT")" && pwd)/$(basename "$DIAG_SCRIPT")"

    OVERRIDE_ENV_LINES="      - DIAG_WITH=${DIAG_WITH}"
    OVERRIDE_ENTRYPOINT='    entrypoint: ["./diag-entrypoint.sh"]'
    append_override_volume "      - ${ABS_DIAG_SCRIPT}:/nethermind/diag-entrypoint.sh:ro"
    echo "[diag] Override: mount diag-entrypoint.sh + set DIAG_WITH=$DIAG_WITH"
elif [ -n "${EXTRA_CLIENT_FLAGS:-}" ]; then
    NEED_OVERRIDE=true
    OVERRIDE_ENTRYPOINT='    entrypoint: ["/bin/sh", "-c", "exec ./nethermind \"$@\" ${EXTRA_CLIENT_FLAGS}", "--"]'
fi

if [ "${GASBENCH_CHECKPOINT_BEFORE_TESTING:-false}" = "true" ]; then
    NEED_OVERRIDE=true
    PATCHED_NLOG_CONFIG="$SCRIPT_DIR/NLog.config.checkpoint"
    TMP_NLOG_CONFIG="${PATCHED_NLOG_CONFIG}.tmp"
    TMP_NLOG_CONTAINER="gasbench-nlog-$$"
    NLOG_IMAGE_VERSION="$(grep -oP '^EC_IMAGE_VERSION=\K.*' "$SCRIPT_DIR/.env" || true)"

    rm -f "$PATCHED_NLOG_CONFIG" "$TMP_NLOG_CONFIG"

    if [ -n "$NLOG_IMAGE_VERSION" ] &&
        docker create --name "$TMP_NLOG_CONTAINER" "$NLOG_IMAGE_VERSION" >/dev/null &&
        docker cp "$TMP_NLOG_CONTAINER:/nethermind/NLog.config" "$TMP_NLOG_CONFIG"; then
        sed 's/autoReload="true"/autoReload="false"/' "$TMP_NLOG_CONFIG" > "$PATCHED_NLOG_CONFIG"
        append_override_volume "      - ${PATCHED_NLOG_CONFIG}:/nethermind/NLog.config:ro"
        echo "[checkpoint] Override: mount patched NLog.config with autoReload=false"
    else
        echo "[checkpoint] WARNING: failed to patch NLog.config; CRIU may fail on NLog FileSystemWatcher" >&2
    fi

    docker rm -f "$TMP_NLOG_CONTAINER" >/dev/null 2>&1 || true
    rm -f "$TMP_NLOG_CONFIG"
fi

if [ -n "${EXTRA_CLIENT_FLAGS:-}" ]; then
    NEED_OVERRIDE=true
    if [ -n "$OVERRIDE_ENV_LINES" ]; then
        OVERRIDE_ENV_LINES="${OVERRIDE_ENV_LINES}
      - EXTRA_CLIENT_FLAGS=${EXTRA_CLIENT_FLAGS}"
    else
        OVERRIDE_ENV_LINES="      - EXTRA_CLIENT_FLAGS=${EXTRA_CLIENT_FLAGS}"
    fi
    echo "[extra-flags] Extra Nethermind flags: ${EXTRA_CLIENT_FLAGS}"
fi

if [ "$NEED_OVERRIDE" = true ]; then
    {
        echo "services:"
        echo "  execution:"
        if [ -n "$OVERRIDE_ENV_LINES" ]; then
            echo "    environment:"
            echo "$OVERRIDE_ENV_LINES"
        fi
        [ -n "$OVERRIDE_ENTRYPOINT" ] && echo "$OVERRIDE_ENTRYPOINT"
        if [ -n "$OVERRIDE_VOLUMES" ]; then
            echo "    volumes:"
            echo "$OVERRIDE_VOLUMES"
        fi
    } > "$SCRIPT_DIR/docker-compose.override.yml"
    echo "[override] Generated docker-compose.override.yml:"
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
