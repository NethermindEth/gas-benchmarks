#!/bin/bash

# Default inputs
WARMUP_OPCODES_PATH="warmup-tests"
CLIENTS="nethermind,geth,reth,besu,erigon,nimbus,ethrex"
RUNS=1
OPCODES_WARMUP_COUNT=1
FILTER=""
IMAGES='{"nethermind":"default","geth":"default","reth":"default","erigon":"default","besu":"default","nimbus":"default","ethrex":"default"}'
EXECUTIONS_FILE="executions.json"
TEST_PATHS_JSON=""
LEGACY_TEST_PATH="eest_tests"
LEGACY_GENESIS_PATH="zkevmgenesis.json"
NETWORK=""
SNAPSHOT_ROOT="snapshots"
OVERLAY_TMP_ROOT="overlay-runtime"
USE_OVERLAY=false
PREPARATION_RESULTS_DIR="prepresults"
RESTART_BEFORE_TESTING=false
SKIP_FORKCHOICE=false
SKIP_EMPTY=true

if [ -f "scripts/common/wait_for_rpc.sh" ]; then
  # shellcheck source=/dev/null
  source "scripts/common/wait_for_rpc.sh"
fi

declare -A ACTIVE_OVERLAY_MOUNTS
declare -A ACTIVE_OVERLAY_UPPERS
declare -A ACTIVE_OVERLAY_WORKS
declare -A ACTIVE_OVERLAY_ROOTS
declare -A ACTIVE_OVERLAY_CLIENTS
declare -A RUNNING_CLIENTS

abspath() {
  python3 - <<'PY' "$1"
import os
import sys
print(os.path.abspath(sys.argv[1]))
PY
}

is_stateful_directory() {
  local dir="$1"
  [ -d "$dir" ] || return 1
  [ -d "$dir/testing" ] || return 1
  return 0
}

collect_stateful_directory() {
  local dir="$1"
  python3 - <<'PY' "$dir"
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
if not root.exists():
    sys.exit(0)

def try_append(path, bucket):
    if path.is_file() and path.suffix == ".txt":
        bucket.append(str(path))

ordered = []

for name in ("gas-bump.txt", "funding.txt", "setup-global-test.txt"):
    try_append(root / name, ordered)

scenario_entries = []

order_file = root / "scenario_order.json"
if order_file.is_file():
    try:
        data = json.loads(order_file.read_text(encoding="utf-8"))
    except Exception:
        data = []
    if isinstance(data, list):
        seen_names = set()
        for item in data:
            if isinstance(item, dict):
                idx = item.get("index")
                name = item.get("name")
            else:
                idx = None
                name = item
            if not isinstance(name, str):
                continue
            name = name.strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            scenario_entries.append((idx, name))

testing_dir = root / "testing"
subdirs = []
if testing_dir.is_dir():
    subdirs = [p for p in sorted(testing_dir.iterdir()) if p.is_dir()]

if subdirs:
    scenario_entries = []
    for scen_dir in subdirs:
        try:
            idx_value = int(scen_dir.name)
        except ValueError:
            idx_value = None
        txt_files = sorted(scen_dir.glob("*.txt"))
        if not txt_files:
            # Fall back to setup/cleanup directories sharing the same index.
            for phase in ("setup", "cleanup"):
                phase_dir = root / phase / scen_dir.name
                if phase_dir.is_dir():
                    txt_files = sorted(phase_dir.glob("*.txt"))
                    if txt_files:
                        break
        if not txt_files:
            # No files found for this scenario yet, skip but preserve ordering gap.
            continue
        for txt in txt_files:
            scenario_entries.append((idx_value, txt.stem))

if not scenario_entries:
    names = []
    if testing_dir.is_dir():
        names = [file.stem for file in sorted(testing_dir.rglob("*.txt"))]
    if not names:
        phases = ("setup", "testing", "cleanup")
        name_set = set()
        for phase in phases:
            phase_dir = root / phase
            if not phase_dir.is_dir():
                continue
            for file in sorted(phase_dir.rglob("*.txt")):
                name_set.add(file.stem)
        names = sorted(name_set)
    scenario_entries = [(None, name) for name in names]

deduped_entries = []
seen = set()
for idx, name in scenario_entries:
    key = (idx, name)
    if key in seen:
        continue
    seen.add(key)
    deduped_entries.append((idx, name))

scenario_entries = deduped_entries


def resolve_path(phase, idx, name):
    candidates = []
    if isinstance(idx, int):
        candidates.append(root / phase / f"{idx:06d}" / f"{name}.txt")
        candidates.append(root / phase / str(idx) / f"{name}.txt")
    if isinstance(idx, str) and idx:
        candidates.append(root / phase / idx / f"{name}.txt")
    candidates.append(root / phase / f"{name}.txt")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None

scenario_entries.sort(key=lambda item: (item[0] if isinstance(item[0], int) else float('inf')))
for idx, name in scenario_entries:
    for phase in ("setup", "testing", "cleanup"):
        path = resolve_path(phase, idx, name)
        if path is not None:
            ordered.append(str(path))
        elif phase == "testing":
            sys.stderr.write(f"[WARN] Missing {phase} file for scenario {name}\n")

for name in ("teardown-global-test.txt", "current-last-global-test.txt"):
    try_append(root / name, ordered)

extra_root = []
for file in sorted(root.glob("*.txt")):
    extra_root.append(str(file))

final = []
seen = set()
for path in ordered + extra_root:
    if path not in seen:
        seen.add(path)
        final.append(path)

# Ensure a trailing NUL so bash read -d '' doesn't drop the last entry.
if final:
    sys.stdout.write("\0".join(final) + "\0")
PY
}

dir_has_content() {
  local dir="$1"
  [ -d "$dir" ] || return 1
  find "$dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null | grep -q .
}

resolve_snapshot_root_for_client() {
  local client="$1"
  local network="$2"
  local root_template="$SNAPSHOT_ROOT"

  if [[ -z "$root_template" ]]; then
    echo ""
    return
  fi

  local client_lower="${client,,}"
  local network_lower="${network,,}"

  root_template="${root_template//<<CLIENT>>/$client}"
  root_template="${root_template//<<client>>/$client_lower}"
  root_template="${root_template//<<Client>>/$client}"

  root_template="${root_template//<<NETWORK>>/$network}"
  root_template="${root_template//<<network>>/$network_lower}"
  root_template="${root_template//<<Network>>/$network}"

  echo "$root_template"
}

restart_client_containers() {
  local client_base="$1"
  local compose_dir="scripts/$client_base"
  local compose_file="$compose_dir/docker-compose.yaml"
  local env_file="$compose_dir/.env"

  if [ ! -f "$compose_file" ]; then
    echo "âš ď¸Ź  Compose file not found for $client_base" >&2
    return 1
  fi

  if [ -f "$env_file" ]; then
    if ! docker compose -f "$compose_file" --env-file "$env_file" restart >/dev/null 2>&1; then
      if ! docker compose -f "$compose_file" --env-file "$env_file" restart; then
        echo "âťŚ Failed to restart services for $client_base" >&2
        return 1
      fi
    fi
  else
    if ! (
      cd "$compose_dir" && docker compose restart
    ); then
      echo "âťŚ Failed to restart services for $client_base" >&2
      return 1
    fi
  fi

  if declare -f wait_for_rpc >/dev/null 2>&1; then
    wait_for_rpc "http://127.0.0.1:8545" 300
  else
    sleep 5
  fi
}

is_measured_file() {
  local file_path="$1"
  local normalized="${file_path//\\/\/}"
  local filename="${file_path##*/}"

  case "$filename" in
    gas-bump.txt|funding.txt|setup-global-test.txt|teardown-global-test.txt)
      return 1 ;;
  esac

  if [[ "$normalized" == */setup/* ]] || [[ "$normalized" == */cleanup/* ]]; then
    return 1
  fi

  if [[ "$normalized" == */testing/* ]]; then
    return 0
  fi

  return 0
}

append_tests_for_path() {
  local base_path="$1"
  local genesis="$2"

  if [ -f "$base_path" ]; then
    TEST_FILES+=("$base_path")
    TEST_TO_GENESIS+=("$genesis")
    return
  fi

  if [ -d "$base_path" ]; then
    if is_stateful_directory "$base_path"; then
      while IFS= read -r -d '' file; do
        TEST_FILES+=("$file")
        TEST_TO_GENESIS+=("$genesis")
      done < <(collect_stateful_directory "$base_path")
    else
      while IFS= read -r -d '' file; do
        TEST_FILES+=("$file")
        TEST_TO_GENESIS+=("$genesis")
      done < <(find "$base_path" -type f -name '*.txt' -print0 | sort -z)
    fi
    return
  fi

  echo "âš ď¸Ź  Test path not found: $base_path" >&2
}

is_mounted() {
  local mount_point="$1"
  local abs_path
  abs_path=$(abspath "$mount_point")
  grep -q " $abs_path " /proc/mounts 2>/dev/null
}

prepare_overlay_for_client() {
  local client="$1"
  local network="$2"
  local snapshot_root="$3"

  local lower=""

  if dir_has_content "$snapshot_root"; then
    lower="$snapshot_root"
  fi

  if [ -z "$lower" ] && [ -n "$network" ] && dir_has_content "$snapshot_root/$network/$client"; then
    lower="$snapshot_root/$network/$client"
  fi

  if [ -z "$lower" ] && [ -n "$network" ] && dir_has_content "$snapshot_root/$network" && [ ! -d "$snapshot_root/$network/$client" ]; then
    lower="$snapshot_root/$network"
  fi

  if [ -z "$lower" ] && dir_has_content "$snapshot_root/$client"; then
    lower="$snapshot_root/$client"
  fi

  if [ -z "$lower" ]; then
    echo "âťŚ Unable to locate snapshot directory for $client under $snapshot_root" >&2
    return 1
  fi

  local overlay_base="$OVERLAY_TMP_ROOT"
  local abs_lower
  abs_lower=$(abspath "$lower")

  if [[ "$overlay_base" != /* ]]; then
    local lower_parent
    lower_parent=$(dirname "$abs_lower")
    overlay_base="$lower_parent/$overlay_base"
  fi

  mkdir -p "$overlay_base"

  local client_root="$overlay_base/$client"
  mkdir -p "$client_root"

  local overlay_id
  overlay_id="$(date +%s%N)_$RANDOM"

  local overlay_root="$client_root/$overlay_id"
  local merged="$overlay_root/merged"
  local upper="$overlay_root/upper"
  local work="$overlay_root/work"

  mkdir -p "$overlay_root"

  if is_mounted "$merged"; then
    if ! umount "$merged" 2>/dev/null; then
      if command -v sudo >/dev/null 2>&1; then
        sudo umount "$merged"
      else
        echo "âťŚ Failed to unmount previous overlay for $client" >&2
        return 1
      fi
    fi
  fi

  rm -rf "$merged" "$upper" "$work"
  mkdir -p "$merged" "$upper" "$work"

  local abs_upper abs_work
  abs_upper=$(abspath "$upper")
  abs_work=$(abspath "$work")
  local mount_opts="lowerdir=$abs_lower,upperdir=$abs_upper,workdir=$abs_work,redirect_dir=on"

  if ! mount -t overlay overlay -o "$mount_opts" "$merged" 2>/dev/null; then
    if command -v sudo >/dev/null 2>&1; then
      sudo mount -t overlay overlay -o "$mount_opts" "$merged"
    else
      echo "âťŚ Failed to mount overlay for $client (need elevated permissions)" >&2
      return 1
    fi
  fi

  ACTIVE_OVERLAY_MOUNTS["$client"]="$merged"
  ACTIVE_OVERLAY_UPPERS["$client"]="$upper"
  ACTIVE_OVERLAY_WORKS["$client"]="$work"
  ACTIVE_OVERLAY_ROOTS["$client"]="$overlay_root"
  ACTIVE_OVERLAY_CLIENTS["$client"]=1

  echo "$merged"
}

cleanup_overlay_for_client() {
  local client="$1"
  local merged="${ACTIVE_OVERLAY_MOUNTS[$client]}"
  local upper="${ACTIVE_OVERLAY_UPPERS[$client]}"
  local work="${ACTIVE_OVERLAY_WORKS[$client]}"
  local root="${ACTIVE_OVERLAY_ROOTS[$client]}"
  local base_dir

  local unmounted=true

  if [ -n "$merged" ] && is_mounted "$merged"; then
    # Try regular unmount first
    if ! umount "$merged" 2>/dev/null; then
      if command -v sudo >/dev/null 2>&1; then
        sudo -n umount "$merged" >/dev/null 2>&1 || sudo -n umount "$merged"
      fi
    fi

    # Fallback to lazy unmount if still mounted
    if is_mounted "$merged"; then
      if ! umount -l "$merged" 2>/dev/null; then
        if command -v sudo >/dev/null 2>&1; then
          sudo -n umount -l "$merged" >/dev/null 2>&1 || sudo -n umount -l "$merged"
        fi
      fi
    fi

    # As a last resort, kill lingering processes and try again
    if is_mounted "$merged" && command -v fuser >/dev/null 2>&1; then
      fuser -km "$merged" >/dev/null 2>&1 || true
      if is_mounted "$merged" && command -v sudo >/dev/null 2>&1; then
        sudo -n fuser -km "$merged" >/dev/null 2>&1 || true
      fi
      sleep 1
      if is_mounted "$merged"; then
        umount "$merged" 2>/dev/null || true
        if is_mounted "$merged" && command -v sudo >/dev/null 2>&1; then
          sudo -n umount "$merged" >/dev/null 2>&1 || true
        fi
      fi
    fi

    if is_mounted "$merged"; then
      echo "⚠️  Unable to unmount overlay for $client ($merged); leaving mount in place" >&2
      unmounted=false
    fi
  fi

  if [ "$unmounted" = true ]; then
    [ -n "$merged" ] && rm -rf "$merged"
    [ -n "$upper" ] && rm -rf "$upper"
    [ -n "$work" ] && rm -rf "$work"
    [ -n "$root" ] && rm -rf "$root"
  fi

  if [ "$unmounted" = true ] && [ -n "$root" ]; then
    local client_root
    client_root=$(dirname "$root")
    if [ -d "$client_root" ] && [ -z "$(ls -A "$client_root" 2>/dev/null)" ]; then
      rmdir "$client_root" 2>/dev/null || true
    fi
    base_dir=$(dirname "$client_root")
    if [ -d "$base_dir" ] && [ -z "$(ls -A "$base_dir" 2>/dev/null)" ]; then
      rmdir "$base_dir" 2>/dev/null || true
    fi
  fi

  unset ACTIVE_OVERLAY_MOUNTS["$client"]
  unset ACTIVE_OVERLAY_UPPERS["$client"]
  unset ACTIVE_OVERLAY_WORKS["$client"]
  unset ACTIVE_OVERLAY_ROOTS["$client"]
  unset ACTIVE_OVERLAY_CLIENTS["$client"]
}

cleanup_all_overlays() {
  local client
  for client in "${!ACTIVE_OVERLAY_CLIENTS[@]}"; do
    cleanup_overlay_for_client "$client"
  done
}

cleanup_stale_overlay_mounts() {
  local base="$OVERLAY_TMP_ROOT"
  if [ -z "$base" ]; then
    return
  fi

  if [[ "$base" != /* ]]; then
    base=$(abspath "$base")
  fi

  if [ ! -d "$base" ]; then
    return
  fi

  mapfile -t stale_mounts < <(mount | awk -v base="$base" '$3 ~ "^" base {print $3}' | sort -r)

  local mount_point
  for mount_point in "${stale_mounts[@]}"; do
    if [ -z "$mount_point" ]; then
      continue
    fi

    if [[ ! "$mount_point" =~ /merged$ ]]; then
      continue
    fi

    if ! umount "$mount_point" 2>/dev/null; then
      if command -v sudo >/dev/null 2>&1; then
        sudo umount "$mount_point" >/dev/null 2>&1 || sudo umount "$mount_point"
      fi
    fi

    if is_mounted "$mount_point"; then
      if ! umount -l "$mount_point" 2>/dev/null; then
        if command -v sudo >/dev/null 2>&1; then
          sudo umount -l "$mount_point" >/dev/null 2>&1 || sudo umount -l "$mount_point"
        fi
      fi
    fi

    if is_mounted "$mount_point" && command -v fuser >/dev/null 2>&1; then
      fuser -km "$mount_point" >/dev/null 2>&1 || true
      if command -v sudo >/dev/null 2>&1; then
        sudo fuser -km "$mount_point" >/dev/null 2>&1 || true
      fi
      sleep 1
      if is_mounted "$mount_point"; then
        umount "$mount_point" 2>/dev/null || true
        if is_mounted "$mount_point" && command -v sudo >/dev/null 2>&1; then
          sudo umount "$mount_point" >/dev/null 2>&1 || sudo umount -l "$mount_point" >/dev/null 2>&1 || true
        fi
      fi
    fi

    if is_mounted "$mount_point"; then
      echo "⚠️  Unable to unmount stale overlay mount $mount_point" >&2
      continue
    fi

    local run_dir
    run_dir=$(dirname "$mount_point")
    rm -rf "$run_dir" 2>/dev/null || true

    local client_dir
    client_dir=$(dirname "$run_dir")
    if [ -d "$client_dir" ] && [ -z "$(ls -A "$client_dir" 2>/dev/null)" ]; then
      rmdir "$client_dir" 2>/dev/null || true
    fi
  done

  find "$base" -mindepth 1 -type d -empty -delete 2>/dev/null || true
}

drop_host_caches() {
  local status=0

  if command -v sync >/dev/null 2>&1; then
    sync || status=$?
  fi

  if [ -w /proc/sys/vm/drop_caches ]; then
    echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || status=$?
    return $status
  fi

  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    echo 3 | sudo -n tee /proc/sys/vm/drop_caches >/dev/null 2>&1 || status=$?
    return $status
  fi

  return 1
}

docker_compose_down_for_client() {
  local client_base="$1"
  local compose_dir="scripts/$client_base"

  if ! command -v docker >/dev/null 2>&1; then
    return
  fi

  if [ -f "$compose_dir/docker-compose.yaml" ]; then
    docker compose -f "$compose_dir/docker-compose.yaml" down --volumes >/dev/null 2>&1 || \
      docker compose -f "$compose_dir/docker-compose.yaml" down --volumes
  elif [ -d "$compose_dir" ]; then
    (
      cd "$compose_dir" && docker compose down --volumes >/dev/null 2>&1 || docker compose down --volumes
    )
  fi
}

docker_container_exists() {
  local name="$1"
  docker ps -a --format '{{.Names}}' | grep -Fxq "$name"
}

dump_client_logs() {
  local client_base="$1"
  if ! command -v docker >/dev/null 2>&1; then
    return
  fi
  mkdir -p logs
  local ts=$(date +%s)
  if docker_container_exists "gas-execution-client"; then
    docker logs gas-execution-client &> "logs/docker_${client_base}_${ts}.log" || true
  fi
  if docker_container_exists "gas-execution-client-sync"; then
    docker logs gas-execution-client-sync &> "logs/docker_sync_${client_base}_${ts}.log" || true
  fi
}

cleanup_on_exit() {
  local exit_status=$?
  trap - EXIT INT TERM

  if command -v docker >/dev/null 2>&1; then
    local client_base client_spec
    if [ "${#RUNNING_CLIENTS[@]}" -gt 0 ]; then
      for client_base in "${!RUNNING_CLIENTS[@]}"; do
        dump_client_logs "$client_base"
        docker_compose_down_for_client "$client_base"
      done
    elif [ "${#CLIENT_ARRAY[@]}" -gt 0 ]; then
      for client_spec in "${CLIENT_ARRAY[@]}"; do
        client_base=$(echo "$client_spec" | cut -d '_' -f 1)
        dump_client_logs "$client_base"
        docker_compose_down_for_client "$client_base"
      done
    fi
  fi

  if declare -F cleanup_all_overlays >/dev/null 2>&1; then
    cleanup_all_overlays
  fi

  if declare -F cleanup_stale_overlay_mounts >/dev/null 2>&1; then
    cleanup_stale_overlay_mounts
  fi

  exit $exit_status
}

# Function to initialize executions.json if it doesn't exist
init_executions_file() {
  if [ ! -f "$EXECUTIONS_FILE" ]; then
    echo "{}" > "$EXECUTIONS_FILE"
    echo "Created $EXECUTIONS_FILE"
  fi
}

# Function to check if client was executed today
was_executed_today() {
  local client=$1
  local today=$(date +%Y-%m-%d)
  if [ ! -f "$EXECUTIONS_FILE" ]; then return 1; fi
  local last_execution=$(jq -r --arg client "$client" '.[$client] // empty' "$EXECUTIONS_FILE" 2>/dev/null | cut -d'T' -f1)
  [ "$last_execution" = "$today" ]
}

# Function to update executions.json with current timestamp
update_execution_time() {
  local client=$1
  local timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  local temp_file=$(mktemp)
  jq --arg client "$client" --arg timestamp "$timestamp" '.[$client] = $timestamp' "$EXECUTIONS_FILE" > "$temp_file" && mv "$temp_file" "$EXECUTIONS_FILE"
  echo "Updated execution time for $client: $timestamp"
}

while getopts "T:t:g:c:r:i:o:f:n:B:R:FW:" opt; do
  case $opt in
    T) TEST_PATHS_JSON="$OPTARG" ;;
    t) LEGACY_TEST_PATH="$OPTARG" ;;
    g) LEGACY_GENESIS_PATH="$OPTARG" ;;
    c) CLIENTS="$OPTARG" ;;
    r) RUNS="$OPTARG" ;;
    i) IMAGES="$OPTARG" ;;
    o) OPCODES_WARMUP_COUNT="$OPTARG" ;;
    f) FILTER="$OPTARG" ;;  # comma-separated exclude patterns
    n) NETWORK="$OPTARG"; USE_OVERLAY=true ;;
    B) SNAPSHOT_ROOT="$OPTARG"; USE_OVERLAY=true ;;
    R) RESTART_BEFORE_TESTING=true;;
    F) SKIP_FORKCHOICE=true;;
    W) WARMUP_OPCODES_PATH="$OPTARG" ;;
    *) echo "Usage: $0 [-t test_path] [-c clients] [-r runs] [-i images] [-o opcodesWarmupCount] [-f filter] [-n network] [-B snapshot_root] [-F skipForkchoice] [-W warmup_opcodes_path]" >&2
       exit 1 ;;
  esac
done



# Allow passing a file path for -T to avoid long argument lists.
if [ -n "$TEST_PATHS_JSON" ]; then
  file_path="$TEST_PATHS_JSON"
  if [[ "$file_path" == @* ]]; then
    file_path="${file_path#@}"
  fi
  if [ -f "$file_path" ]; then
    TEST_PATHS_JSON=$(cat "$file_path")
  fi
fi

# Fallback to legacy -t/-g if -T not provided
if [ -z "$TEST_PATHS_JSON" ]; then
  if [ -z "$LEGACY_TEST_PATH" ]; then
    echo "âťŚ You must provide either -T <json> or -t <test_path>"
    exit 1
  fi

  echo "Falling back to legacy mode with -t and -g"
  if [ -n "$LEGACY_GENESIS_PATH" ]; then
    TEST_PATHS_JSON="[ {\"path\": \"$LEGACY_TEST_PATH\", \"genesis\": \"$LEGACY_GENESIS_PATH\"} ]"
  else
    TEST_PATHS_JSON="[ {\"path\": \"$LEGACY_TEST_PATH\"} ]"
  fi
fi

# Parse TEST_PATHS_JSON into arrays
TEST_PATHS=()
GENESIS_PATHS=()
count=$(echo "$TEST_PATHS_JSON" | jq length)
for i in $(seq 0 $((count - 1))); do
  path=$(echo "$TEST_PATHS_JSON" | jq -r ".[$i].path")
  genesis=$(echo "$TEST_PATHS_JSON" | jq -r ".[$i].genesis // empty")
  TEST_PATHS+=("$path")
  GENESIS_PATHS+=("$genesis")
done

IFS=',' read -ra CLIENT_ARRAY <<< "$CLIENTS"
IFS=',' read -ra RAW_FILTERS <<< "$FILTER"
FILTERS=()
for raw_filter in "${RAW_FILTERS[@]}"; do
  trimmed="${raw_filter#"${raw_filter%%[![:space:]]*}"}"
  trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
  if [ -n "$trimmed" ]; then
    FILTERS+=("$trimmed")
  fi
done

FILTER_ACTIVE=false
if [ "${#FILTERS[@]}" -gt 0 ]; then
  FILTER_ACTIVE=true
fi
declare -A SCENARIO_FILTER_CACHE=()
declare -A SCENARIO_SKIP_LOGGED=()

trap cleanup_on_exit EXIT INT TERM

mkdir -p results warmupresults logs

# Initialize debug file if specified
if [ "$SKIP_FORKCHOICE" = true ]; then
  SKIP_FORKCHOICE_OPT=" --skipForkchoice"
else
  SKIP_FORKCHOICE_OPT=""
fi

if [ "$USE_OVERLAY" = true ]; then
  cleanup_stale_overlay_mounts
  if [[ "$OVERLAY_TMP_ROOT" = /* ]]; then
    mkdir -p "$OVERLAY_TMP_ROOT"
  fi
fi

# Set up environment
rm -rf results
mkdir -p results
mkdir -p warmupresults
mkdir -p logs
rm -rf "$PREPARATION_RESULTS_DIR"
mkdir -p "$PREPARATION_RESULTS_DIR"

# Initialize executions tracking
init_executions_file

# Install dependencies
pip install -r requirements.txt
make prepare_tools

# Find test files and their associated genesis paths
TEST_FILES=()
TEST_TO_GENESIS=()

for i in "${!TEST_PATHS[@]}"; do
  path="${TEST_PATHS[$i]}"
  genesis="${GENESIS_PATHS[$i]}"
  append_tests_for_path "$path" "$genesis"
done

DEFAULT_GENESIS=""
for genesis_entry in "${TEST_TO_GENESIS[@]}"; do
  if [ -n "$genesis_entry" ]; then
    DEFAULT_GENESIS="$genesis_entry"
    break
  fi
done

# Run benchmarks
for run in $(seq 1 $RUNS); do
  for client in "${CLIENT_ARRAY[@]}"; do
    
    # Skip nimbus or ethrex if already run today
    if { [ "$client" = "nimbus" ] || [ "$client" = "ethrex" ]; } && was_executed_today "$client"; then
      echo "Skipping $client - already executed today"
      continue
    fi

    client_base=$(echo "$client" | cut -d '_' -f 1)
    raw_genesis="$DEFAULT_GENESIS"
    genesis_client="$client_base"

    if [ -n "$raw_genesis" ]; then
      if [ "$genesis_client" != "besu" ] && [ "$genesis_client" != "nethermind" ]; then
        genesis_client="geth"
      fi
      genesis_path="scripts/genesisfiles/$genesis_client/$raw_genesis"
    else
      genesis_path=""
    fi

    data_dir=""
    if [ "$USE_OVERLAY" = true ]; then
      snapshot_root_for_client=$(resolve_snapshot_root_for_client "$client_base" "$NETWORK")
      if [ -z "$snapshot_root_for_client" ]; then
        echo "âťŚ Snapshot root not specified for $client" >&2
        cleanup_overlay_for_client "$client_base"
        continue
      fi
      data_dir=$(prepare_overlay_for_client "$client_base" "$NETWORK" "$snapshot_root_for_client") || {
        echo "âťŚ Skipping $client - overlay setup failed" >&2
        cleanup_overlay_for_client "$client_base"
        continue
      }
    else
      data_dir=$(abspath "scripts/$client_base/execution-data")
      mkdir -p "$data_dir"
    fi

    volume_name="${client_base}_$(date +%s)_$RANDOM"
    if [ "$USE_OVERLAY" = true ]; then
      overlay_root="${ACTIVE_OVERLAY_ROOTS[$client_base]}"
      if [ -n "$overlay_root" ]; then
        overlay_token=$(basename "$overlay_root")
        volume_name="${client_base}_${overlay_token}_$(date +%s)_$RANDOM"
      fi
    fi
    volume_name=$(echo "$volume_name" | tr -cd '[:alnum:]._-')
    if [ -z "$volume_name" ]; then
      volume_name="${client_base}_volume"
    fi


    setup_cmd=(python3 setup_node.py --client "$client" --imageBulk "$IMAGES" --dataDir "$data_dir")
    if [ -n "$NETWORK" ]; then
      setup_cmd+=(--network "$NETWORK")
    fi
    if [ -z "$NETWORK" ] && [ -n "$genesis_path" ]; then
      echo "Using custom genesis for $client: $genesis_path"
      setup_cmd+=(--genesisPath "$genesis_path")
    fi
    setup_cmd+=(--volumeName "$volume_name")

    RUNNING_CLIENTS["$client_base"]=1

    echo "[INFO] Running setup_node command: ${setup_cmd[*]}"
    "${setup_cmd[@]}"

    if declare -f wait_for_rpc >/dev/null 2>&1; then
      wait_for_rpc "http://127.0.0.1:8545" 300
    else
      sleep 5
    fi

    python3 -c "from utils import print_computer_specs; print(print_computer_specs())" > results/computer_specs.txt
    cat results/computer_specs.txt

    declare -A warmup_run_counts=()

    for i in "${!TEST_FILES[@]}"; do
      test_file="${TEST_FILES[$i]}"
      normalized_path="${test_file//\\/\/}"
      filename="${test_file##*/}"
      if is_measured_file "$test_file"; then
        measured=true
      else
        measured=false
      fi

      apply_filter=false
      if [ "$FILTER_ACTIVE" = true ]; then
        if [ "$measured" = true ] || [[ "$normalized_path" == */setup/* ]] || [[ "$normalized_path" == */cleanup/* ]]; then
          apply_filter=true
        fi
      fi

      if [ "$apply_filter" = true ]; then
        scenario_key="${filename,,}"
        match="${SCENARIO_FILTER_CACHE[$scenario_key]}"

        if [ -z "$match" ]; then
          match=0
          for pat in "${FILTERS[@]}"; do
            pat_lc="${pat,,}"

            if [[ "$scenario_key" == *"$pat_lc"* ]]; then
              match=1
              break
            fi
          done
          SCENARIO_FILTER_CACHE["$scenario_key"]="$match"
        fi

        if [ "$match" -ne 1 ]; then
          if [ -z "${SCENARIO_SKIP_LOGGED[$scenario_key]}" ]; then
            echo "Skipping scenario $filename (does not match case-insensitive filter)"
            SCENARIO_SKIP_LOGGED["$scenario_key"]=1
          fi
          continue
        fi
      fi

      if [ "$measured" = false ]; then
        echo "Executing preparation script (not measured): $filename"
        echo "[INFO] Running preparation run_kute command: python3 run_kute.py --output \"$PREPARATION_RESULTS_DIR\" --testsPath \"$test_file\" --jwtPath /tmp/jwtsecret --client $client --rerunSyncing --run $run$SKIP_FORKCHOICE_OPT"
        python3 run_kute.py --output "$PREPARATION_RESULTS_DIR" --testsPath "$test_file" --jwtPath /tmp/jwtsecret --client $client --rerunSyncing --run $run$SKIP_FORKCHOICE_OPT
        echo ""
        continue
      fi

      if [ "$RESTART_BEFORE_TESTING" = true ]; then
        if ! restart_client_containers "$client_base"; then
          echo "âš ď¸Ź  Skipping $filename for $client - restart failed" >&2
          continue
        fi
      fi

      base_prefix="${filename%-gas-value_*}"

      IFS=',' read -ra _warmup_roots <<< "$WARMUP_OPCODES_PATH"
      warmup_path=""
      rel_path="$normalized_path"
      rel_path="${rel_path#./}"
      rel_path="${rel_path#/}"
      esc_filename="$filename"
      esc_filename=${esc_filename//\\/\\\\}
      esc_filename=${esc_filename//\[/\\[}
      esc_filename=${esc_filename//\]/\\]}
      esc_filename=${esc_filename//\*/\\*}
      esc_filename=${esc_filename//\?/\\?}
      for root in "${_warmup_roots[@]}"; do
        [ -z "$root" ] && continue
        candidate="$root/$rel_path"
        if [ -f "$candidate" ]; then
          warmup_path="$candidate"
          break
        fi
        found=$(find "$root" -type f -name "$esc_filename" -print -quit 2>/dev/null)
        if [ -n "$found" ]; then
          warmup_path="$found"
          break
        fi
      done
      # Legacy fallback: try matching <base_prefix> alongside the test
      if [ -z "$warmup_path" ] && [ "$base_prefix" != "$filename" ]; then
        rel_dir="${rel_path%/*}"
        [ "$rel_dir" = "$rel_path" ] && rel_dir=""
        esc_base_prefix="$base_prefix"
        esc_base_prefix=${esc_base_prefix//\\/\\\\}
        esc_base_prefix=${esc_base_prefix//\[/\\[}
        esc_base_prefix=${esc_base_prefix//\]/\\]}
        esc_base_prefix=${esc_base_prefix//\*/\\*}
        esc_base_prefix=${esc_base_prefix//\?/\\?}
        for root in "${_warmup_roots[@]}"; do
          [ -z "$root" ] && continue
          search_dir="$root"
          [ -n "$rel_dir" ] && search_dir="$root/$rel_dir"
          found=$(find "$search_dir" -maxdepth 1 -type f -name "$esc_base_prefix" -print -quit 2>/dev/null)
          if [ -n "$found" ]; then
            warmup_path="$found"
            break
          fi
        done
      fi

      if (( OPCODES_WARMUP_COUNT > 0 )); then
        if [ -z "$warmup_path" ]; then
          echo "[WARN] No opcode warmup file found for $filename (searched under $WARMUP_OPCODES_PATH)"
        else
          current_count="${warmup_run_counts["$warmup_path"]:-0}"
          case "$current_count" in
            ''|*[!0-9]*) current_count=0 ;;
          esac
          if (( current_count < OPCODES_WARMUP_COUNT )); then
            for warmup_count in $(seq 1 $OPCODES_WARMUP_COUNT); do
              echo "[INFO] Running opcode warmup run_kute command: python3 run_kute.py --output warmupresults --testsPath \"$warmup_path\" --jwtPath /tmp/jwtsecret --client $client --run $run --kuteArguments '-f engine_newPayload'$SKIP_FORKCHOICE_OPT"
              python3 run_kute.py --output warmupresults --testsPath "$warmup_path" --jwtPath /tmp/jwtsecret --client $client --run $run --kuteArguments '-f engine_newPayload'$SKIP_FORKCHOICE_OPT
              current_count=$((current_count + 1))
            done
            warmup_run_counts["$warmup_path"]=$current_count
          fi
        fi
      fi

      # Actual measured run
      drop_host_caches || true
      echo "[INFO] Running measured run_kute command: python3 run_kute.py --output results --testsPath \"$test_file\" --jwtPath /tmp/jwtsecret --client $client --run $run$SKIP_FORKCHOICE_OPT"
      python3 run_kute.py --output results --testsPath "$test_file" --jwtPath /tmp/jwtsecret --client $client --run $run$SKIP_FORKCHOICE_OPT

      # Capture debug_traceBlockByNumber for the testing payload (unigramTracer) when enabled
      if [ "${TRACE_BLOCKS:-false}" = true ]; then
        trace_block_number=$(python3 - "$test_file" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
block = ""
try:
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        method = str(data.get("method", ""))
        if not method.startswith("engine_newPayload"):
            continue
        params = data.get("params") or []
        if params and isinstance(params[0], dict):
            bn = params[0].get("blockNumber")
            if isinstance(bn, str) and bn.strip():
                block = bn.strip()
            elif isinstance(bn, (int, float)):
                block = hex(int(bn))
        break
except Exception:
    block = ""

# Sanitize for filename
if block:
    fname = block.replace("0x", "0x")
    print(fname)
else:
    print("")
PY
)

        if [ -n "$trace_block_number" ]; then
          mkdir -p traces
          trace_file="traces/block_${trace_block_number}.json"
          trace_payload=$(cat <<EOF
{"jsonrpc":"2.0","id":1,"method":"debug_traceBlockByNumber","params":["$trace_block_number",{"tracer":"unigramTracer"}]}
EOF
)
          echo "[INFO] Capturing unigramTracer trace for block $trace_block_number into $trace_file"
          if ! curl -s -X POST -H "Content-Type: application/json" --data "$trace_payload" http://127.0.0.1:8545 > "$trace_file"; then
            echo "[WARN] Failed to capture trace for block $trace_block_number"
            rm -f "$trace_file"
          fi
        else
          echo "[WARN] Could not determine blockNumber for $filename; skipping debug_traceBlockByNumber"
        fi
      fi

      echo "" # Line break after each test for logs clarity
    done

    # Collect logs & teardown
    ts=$(date +%s)
    dump_client_logs "$client_base"
    docker_compose_down_for_client "$client_base"

    rm -rf "scripts/$client_base/execution-data"

    if [ "$USE_OVERLAY" = true ]; then
      cleanup_overlay_for_client "$client_base"
      cleanup_stale_overlay_mounts
    fi

    unset RUNNING_CLIENTS["$client_base"]

    drop_host_caches || true

    # Only mark the client as executed after the final run to avoid skipping
    # subsequent runs within the same invocation when RUNS > 1.
    if [ "$run" -eq "$RUNS" ]; then
      update_execution_time "$client"
    fi
  done
done

SKIP_EMPTY_OPT=""
if [ "$SKIP_EMPTY" = true ]; then
  SKIP_EMPTY_OPT="--skipEmpty"
fi

if [ -z "$IMAGES" ]; then
  python3 report_tables.py --resultsPath results --clients "$CLIENTS" --testsPath "${TEST_PATHS[0]}" --runs "$RUNS" $SKIP_EMPTY_OPT
  python3 report_html.py   --resultsPath results --clients "$CLIENTS" --testsPath "${TEST_PATHS[0]}" --runs "$RUNS" $SKIP_EMPTY_OPT
else
  python3 report_tables.py --resultsPath results --clients "$CLIENTS" --testsPath "${TEST_PATHS[0]}" --runs "$RUNS" --images "$IMAGES" $SKIP_EMPTY_OPT
  python3 report_html.py   --resultsPath results --clients "$CLIENTS" --testsPath "${TEST_PATHS[0]}" --runs "$RUNS" --images "$IMAGES" $SKIP_EMPTY_OPT
fi

# Prepare and zip the results
mkdir -p reports/docker
cp -r results/docker_* reports/docker
zip -r reports.zip reports

# Print timing summary at the end
