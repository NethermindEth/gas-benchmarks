#!/bin/bash
# Run benchmarks with Nethermind started natively from source (no Docker EL).
#
# Usage: same as run.sh
#   ./run-native.sh -f "keccak" -t eest_tests -c nethermind -r 1
#
# Optional environment variables:
#   NETHERMIND_REPO: path to Nethermind repo (default: C:\Users\kamil\source\repos\nethermind)
#   SKIP_PREPARE_TOOLS=true: skip `make prepare_tools` inside run.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.nethermind_native.pid"
NATIVE_BIN_DIR="$SCRIPT_DIR/.native-bin"

to_unix_path() {
  local input="$1"
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -u "$input"
  elif command -v wslpath >/dev/null 2>&1; then
    wslpath -u "$input"
  else
    echo "$input"
  fi
}

stop_pid_if_running() {
  local pid="$1"
  [ -n "$pid" ] || return 0
  if [ "$IS_WINDOWS" = true ]; then
    taskkill //F //PID "$pid" >/dev/null 2>&1 || true
  else
    kill "$pid" >/dev/null 2>&1 || true
    sleep 1
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi
}

EXE_EXT=""
IS_WINDOWS=false
IS_WSL=false
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    IS_WINDOWS=true
    EXE_EXT=".exe"
    ;;
esac
if grep -qi microsoft /proc/version 2>/dev/null || [ -n "${WSL_DISTRO_NAME:-}" ]; then
  IS_WSL=true
fi

cleanup() {
  if [ -f "$PID_FILE" ]; then
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "${pid:-}" ]; then
      echo "[run-native] Stopping Nethermind (PID $pid)"
      stop_pid_if_running "$pid"
    fi
    rm -f "$PID_FILE"
  fi
  rm -rf "$NATIVE_BIN_DIR" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

mkdir -p "$NATIVE_BIN_DIR"
cat > "$NATIVE_BIN_DIR/docker" <<'DOCKERSHIM'
#!/bin/bash
exit 0
DOCKERSHIM
chmod +x "$NATIVE_BIN_DIR/docker" >/dev/null 2>&1 || true

if ! python3 --version >/dev/null 2>&1 && python --version >/dev/null 2>&1; then
  cat > "$NATIVE_BIN_DIR/python3" <<'PYSHIM'
#!/bin/bash
exec python "$@"
PYSHIM
  chmod +x "$NATIVE_BIN_DIR/python3" >/dev/null 2>&1 || true
fi

export PATH="$NATIVE_BIN_DIR:$PATH"
export GB_NATIVE_SKIP_RUN_FOR_CLIENTS="nethermind"

DEFAULT_REPO="C:\\Users\\kamil\\source\\repos\\nethermind"
NETHERMIND_REPO="${NETHERMIND_REPO:-$DEFAULT_REPO}"
NETHERMIND_REPO="$(to_unix_path "$NETHERMIND_REPO")"

if [ ! -d "$NETHERMIND_REPO" ]; then
  echo "[run-native] ERROR: Nethermind repo path not found: $NETHERMIND_REPO"
  exit 1
fi

RUNNER_PROJECT="$NETHERMIND_REPO/src/Nethermind/Nethermind.Runner"
RUNNER_BIN_BASE="$NETHERMIND_REPO/src/Nethermind/artifacts/bin/Nethermind.Runner/release/nethermind"
RUNNER_BIN="$RUNNER_BIN_BASE${EXE_EXT}"
DATA_DIR="$SCRIPT_DIR/scripts/nethermind/execution-data"
JWT_SECRET="$SCRIPT_DIR/scripts/nethermind/jwtsecret"
CHAINSPEC="$SCRIPT_DIR/scripts/genesisfiles/nethermind/zkevmgenesis.json"
LOG_FILE="$SCRIPT_DIR/scripts/nethermind/nethermind_native.log"

if [ ! -d "$RUNNER_PROJECT" ]; then
  echo "[run-native] ERROR: Runner project not found: $RUNNER_PROJECT"
  exit 1
fi

if [ -f "$PID_FILE" ]; then
  stale_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${stale_pid:-}" ]; then
    echo "[run-native] Stopping stale Nethermind PID $stale_pid"
    stop_pid_if_running "$stale_pid"
  fi
  rm -f "$PID_FILE"
fi

if [ "$IS_WSL" = true ] && [ -f "$RUNNER_BIN_BASE.exe" ]; then
  # Prefer Windows runner under WSL to avoid Linux .NET runtime requirements.
  RUNNER_BIN="$RUNNER_BIN_BASE.exe"
fi
if [ ! -f "$RUNNER_BIN" ] && [ -f "$RUNNER_BIN_BASE.exe" ]; then
  RUNNER_BIN="$RUNNER_BIN_BASE.exe"
fi
if [ ! -f "$RUNNER_BIN" ] && [ -f "$RUNNER_BIN_BASE" ]; then
  RUNNER_BIN="$RUNNER_BIN_BASE"
fi

if [ ! -f "$RUNNER_BIN" ]; then
  echo "[run-native] Building Nethermind.Runner from $NETHERMIND_REPO"
  dotnet build "$RUNNER_PROJECT" -c Release --property WarningLevel=0
fi

if [ ! -f "$RUNNER_BIN" ] && [ -f "$RUNNER_BIN_BASE.exe" ]; then
  RUNNER_BIN="$RUNNER_BIN_BASE.exe"
fi
if [ ! -f "$RUNNER_BIN" ] && [ -f "$RUNNER_BIN_BASE" ]; then
  RUNNER_BIN="$RUNNER_BIN_BASE"
fi

if [ ! -f "$RUNNER_BIN" ]; then
  echo "[run-native] ERROR: Built runner not found: $RUNNER_BIN"
  exit 1
fi

RUNNER_IS_WINDOWS=false
case "${RUNNER_BIN,,}" in
  *.exe) RUNNER_IS_WINDOWS=true ;;
esac

# On WSL drvfs with metadata enabled, Windows .exe files may not be executable
# unless the x-bit is present.
if [ ! -x "$RUNNER_BIN" ]; then
  chmod +x "$RUNNER_BIN" 2>/dev/null || true
fi

mkdir -p "$DATA_DIR"
cp "$JWT_SECRET" /tmp/jwtsecret

DATA_DIR_ARG="$DATA_DIR"
CHAINSPEC_ARG="$CHAINSPEC"
JWT_SECRET_ARG="/tmp/jwtsecret"
if [ "$RUNNER_IS_WINDOWS" = true ] && command -v wslpath >/dev/null 2>&1; then
  DATA_DIR_ARG="$(wslpath -w "$DATA_DIR")"
  CHAINSPEC_ARG="$(wslpath -w "$CHAINSPEC")"
  JWT_SECRET_ARG="$(wslpath -w "$JWT_SECRET")"
fi

echo "[run-native] Starting native Nethermind from $RUNNER_BIN"
"$RUNNER_BIN" \
  --config=none \
  --datadir="$DATA_DIR_ARG" \
  --JsonRpc.Enabled=true \
  --JsonRpc.Host=0.0.0.0 \
  --JsonRpc.Port=8545 \
  --JsonRpc.JwtSecretFile="$JWT_SECRET_ARG" \
  --JsonRpc.EngineHost=0.0.0.0 \
  --JsonRpc.EnginePort=8551 \
  --JsonRpc.EnabledModules="[Debug,Eth,Subscribe,Trace,TxPool,Web3,Personal,Proof,Net,Parity,Health,Rpc,Testing]" \
  --Network.DiscoveryPort=0 \
  --Network.MaxActivePeers=0 \
  --Init.DiscoveryEnabled=false \
  --HealthChecks.Enabled=true \
  --Metrics.Enabled=true \
  --Metrics.ExposePort=8008 \
  --Sync.MaxAttemptsToUpdatePivot=0 \
  --Init.AutoDump=None \
  --Merge.NewPayloadBlockProcessingTimeout=70000 \
  --Merge.TerminalTotalDifficulty=0 \
  --Init.LogRules=Consensus.Processing.ProcessingStats:Debug \
  --Init.ChainSpecPath="$CHAINSPEC_ARG" \
  > "$LOG_FILE" 2>&1 &

NETHERMIND_PID=$!
echo "$NETHERMIND_PID" > "$PID_FILE"
echo "[run-native] Nethermind PID: $NETHERMIND_PID"

source "$SCRIPT_DIR/scripts/common/wait_for_rpc.sh"
echo "[run-native] Waiting for RPC at http://127.0.0.1:8545"
if ! wait_for_rpc "http://127.0.0.1:8545" 15; then
  echo "[run-native] RPC failed to start. Last log lines:"
  tail -50 "$LOG_FILE" 2>/dev/null || true
  exit 1
fi

bash "$SCRIPT_DIR/run.sh" "$@"
