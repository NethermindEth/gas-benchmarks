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

get_env_value() {
    local key="$1"
    grep -oP "^${key}=\\K.*" "$SCRIPT_DIR/.env" 2>/dev/null || true
}

podman_cmd() {
    if [ "${PODMAN_ROOTFUL:-true}" = "true" ] && command -v sudo >/dev/null 2>&1; then
        sudo podman "$@"
    else
        podman "$@"
    fi
}

qualify_podman_image() {
    local image="$1"
    local first="${image%%/*}"
    if [[ "$image" == *"/"* && "$first" != *"."* && "$first" != *":"* && "$first" != "localhost" ]]; then
        echo "docker.io/$image"
    else
        echo "$image"
    fi
}

if [ -f "$SCRIPT_DIR/.env" ]; then
    EXTRA_CLIENT_FLAGS="$(get_env_value EXTRA_CLIENT_FLAGS)"
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
    NLOG_IMAGE_VERSION="$(get_env_value EC_IMAGE_VERSION)"
    if [ -n "$NLOG_IMAGE_VERSION" ] && [ "${CONTAINER_RUNTIME:-docker}" = "podman" ]; then
        NLOG_IMAGE_VERSION="$(qualify_podman_image "$NLOG_IMAGE_VERSION")"
    fi

    rm -f "$PATCHED_NLOG_CONFIG" "$TMP_NLOG_CONFIG"

    if [ -n "$NLOG_IMAGE_VERSION" ] && [ "${CONTAINER_RUNTIME:-docker}" = "podman" ] &&
        podman_cmd create --name "$TMP_NLOG_CONTAINER" "$NLOG_IMAGE_VERSION" >/dev/null &&
        podman_cmd cp "$TMP_NLOG_CONTAINER:/nethermind/NLog.config" "$TMP_NLOG_CONFIG"; then
        sed 's/autoReload="true"/autoReload="false"/' "$TMP_NLOG_CONFIG" > "$PATCHED_NLOG_CONFIG"
        append_override_volume "      - ${PATCHED_NLOG_CONFIG}:/nethermind/NLog.config:ro"
        echo "[checkpoint] Override: mount patched NLog.config with autoReload=false"
    elif [ -n "$NLOG_IMAGE_VERSION" ] &&
        docker create --name "$TMP_NLOG_CONTAINER" "$NLOG_IMAGE_VERSION" >/dev/null &&
        docker cp "$TMP_NLOG_CONTAINER:/nethermind/NLog.config" "$TMP_NLOG_CONFIG"; then
        sed 's/autoReload="true"/autoReload="false"/' "$TMP_NLOG_CONFIG" > "$PATCHED_NLOG_CONFIG"
        append_override_volume "      - ${PATCHED_NLOG_CONFIG}:/nethermind/NLog.config:ro"
        echo "[checkpoint] Override: mount patched NLog.config with autoReload=false"
    else
        echo "[checkpoint] WARNING: failed to patch NLog.config; CRIU may fail on NLog FileSystemWatcher" >&2
    fi

    if [ "${CONTAINER_RUNTIME:-docker}" = "podman" ]; then
        podman_cmd rm -f "$TMP_NLOG_CONTAINER" >/dev/null 2>&1 || true
    else
        docker rm -f "$TMP_NLOG_CONTAINER" >/dev/null 2>&1 || true
    fi
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

if [ "${CONTAINER_RUNTIME:-docker}" = "podman" ]; then
    EC_IMAGE_VERSION="$(get_env_value EC_IMAGE_VERSION)"
    EC_IMAGE_VERSION="$(qualify_podman_image "$EC_IMAGE_VERSION")"
    EC_DATA_DIR="$(get_env_value EC_DATA_DIR)"
    EC_JWT_SECRET_PATH="$(get_env_value EC_JWT_SECRET_PATH)"
    CHAINSPEC_PATH="$(get_env_value CHAINSPEC_PATH)"
    EC_VOLUME_NAME="$(get_env_value EC_VOLUME_NAME)"
    NETHERMIND_CONFIG_FLAG="$(get_env_value NETHERMIND_CONFIG_FLAG)"
    NETHERMIND_GENESIS_FLAG="$(get_env_value NETHERMIND_GENESIS_FLAG)"
    NETHERMIND_GENESIS_FLAG="${NETHERMIND_GENESIS_FLAG:---log=INFO}"

    if [ -z "$EC_IMAGE_VERSION" ] || [ -z "$EC_DATA_DIR" ] || [ -z "$EC_JWT_SECRET_PATH" ] || [ -z "$CHAINSPEC_PATH" ]; then
        echo "[podman] Missing required .env values for Nethermind startup" >&2
        exit 1
    fi

    mkdir -p "$EC_DATA_DIR" "$SCRIPT_DIR/diagfiles"
    podman_cmd rm -f gas-execution-client >/dev/null 2>&1 || true
    podman_cmd network exists gas-network >/dev/null 2>&1 || podman_cmd network create gas-network >/dev/null

    NLOG_VOLUME_ARGS=()
    if [ -n "${PATCHED_NLOG_CONFIG:-}" ] && [ -f "$PATCHED_NLOG_CONFIG" ]; then
        NLOG_VOLUME_ARGS=(-v "$PATCHED_NLOG_CONFIG:/nethermind/NLog.config:ro")
    fi

    ENTRYPOINT_ARGS=()
    if [ -n "${DIAG_WITH:-}" ]; then
        DIAG_SCRIPT="$SCRIPT_DIR/diag-entrypoint.sh"
        chmod +x "$DIAG_SCRIPT"
        ENTRYPOINT_ARGS=(--entrypoint ./diag-entrypoint.sh -e "DIAG_WITH=${DIAG_WITH}" -v "$DIAG_SCRIPT:/nethermind/diag-entrypoint.sh:ro")
    fi

    command_args=(
        "${NETHERMIND_CONFIG_FLAG:---config=none}"
        "--datadir=/nethermind/data"
        "--JsonRpc.Enabled=true"
        "--JsonRpc.Host=0.0.0.0"
        "--JsonRpc.Port=8545"
        "--JsonRpc.JwtSecretFile=/tmp/jwt/jwtsecret"
        "--JsonRpc.EngineHost=0.0.0.0"
        "--JsonRpc.EnginePort=8551"
        "--JsonRpc.EnabledModules=[Debug,Eth,Subscribe,Trace,TxPool,Web3,Personal,Proof,Net,Parity,Health,Rpc,Testing]"
        "--Network.DiscoveryPort=0"
        "--Network.MaxActivePeers=0"
        "--Init.DiscoveryEnabled=false"
        "--HealthChecks.Enabled=true"
        "--Metrics.Enabled=true"
        "--Metrics.ExposePort=8008"
        "--Init.GenesisHash=0x9cbea0de83b440f4462c8280a4b0b4590cdb452069757e2c510cb3456b6c98cc"
        "--Sync.MaxAttemptsToUpdatePivot=0"
        "--Init.AutoDump=None"
        "--Pruning.PruningBoundary=2000"
        "--Merge.NewPayloadBlockProcessingTimeout=70000"
        "--Merge.TerminalTotalDifficulty=0"
        "--Init.LogRules=Consensus.Processing.ProcessingStats:Debug"
        "--Blocks.CachePrecompilesOnBlockProcessing=false"
        "--Init.BaseDbPath=/nethermind/data/mainnet"
        "--FlatDb.Enabled=true"
        "$NETHERMIND_GENESIS_FLAG"
    )

    if [ -n "${EXTRA_CLIENT_FLAGS:-}" ]; then
        read -r -a extra_args <<< "$EXTRA_CLIENT_FLAGS"
        command_args+=("${extra_args[@]}")
    fi

    podman_cmd pull "$EC_IMAGE_VERSION"
    podman_cmd run -d \
        --name gas-execution-client \
        --replace \
        --tty \
        --network gas-network \
        -e TERM=xterm-256color \
        -e COLORTERM=truecolor \
        -e "DOTNET_USE_POLLING_FILE_WATCHER=${DOTNET_USE_POLLING_FILE_WATCHER:-false}" \
        -v "$EC_DATA_DIR:/nethermind/data:rw" \
        -v "$EC_JWT_SECRET_PATH:/tmp/jwt/jwtsecret:ro" \
        -v "$CHAINSPEC_PATH:/tmp/chainspec/chainspec.json:ro" \
        -v "$SCRIPT_DIR/diagfiles:/nethermind/diag:rw" \
        "${NLOG_VOLUME_ARGS[@]}" \
        "${ENTRYPOINT_ARGS[@]}" \
        -p 8009:8009 \
        -p 8545:8545 \
        -p 8551:8551 \
        --label metrics_enabled=true \
        --label metrics_port=8008 \
        --label metrics_path=/metrics \
        --label logs_enabled=false \
        --label instance="${GA_METRICS_LABELS_INSTANCE:-}" \
        "$EC_IMAGE_VERSION" \
        "${command_args[@]}"

    echo "Invoking wait_for_rpc for Nethermind RPC readiness..."
    if ! wait_for_rpc "http://0.0.0.0:8545" 300; then
        echo "RPC failed to start. Dumping logs..."
        podman_cmd logs gas-execution-client || true
        exit 1
    fi

    podman_cmd logs gas-execution-client || true
    exit 0
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
