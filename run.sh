#!/bin/bash

# Default inputs
WARMUP_OPCODES_PATH="warmup-tests"
WARMUP_FILE=""
CLIENTS="nethermind,geth,reth,besu,erigon,nimbus,ethrex"
RUNS=1
OPCODES_WARMUP_COUNT=1
FILTER=""
IMAGES='{"nethermind":"default","geth":"default","reth":"default","erigon":"default","besu":"default","nimbus":"default","ethrex":"default"}'
EXECUTIONS_FILE="executions.json"
TEST_PATHS_JSON=""
LEGACY_TEST_PATH="eest_tests"
LEGACY_GENESIS_PATH="zkevm_genesis.json"
DEBUG=false
DEBUG_FILE=""
PROFILE_TEST=false
NETWORK=""
SNAPSHOT_ROOT="snapshots"
OVERLAY_TMP_ROOT="overlay-runtime"
USE_OVERLAY=false
PREPARATION_RESULTS_DIR="prepresults"
RESTART_BEFORE_TESTING=false

if [ -f "scripts/common/wait_for_rpc.sh" ]; then
  # shellcheck source=/dev/null
  source "scripts/common/wait_for_rpc.sh"
fi

# Timing variables
declare -A STEP_TIMES
declare -A ACTIVE_OVERLAY_MOUNTS
declare -A ACTIVE_OVERLAY_UPPERS
declare -A ACTIVE_OVERLAY_WORKS
declare -A ACTIVE_OVERLAY_CLIENTS
declare -A RUNNING_CLIENTS
SCRIPT_START_TIME=$(date +%s.%N)

# Debug logging function
debug_log() {
  if [ "$DEBUG" = true ]; then
    local message="[DEBUG] $1"
    echo "$message"
    if [ -n "$DEBUG_FILE" ]; then
      echo "$message" >> "$DEBUG_FILE"
    fi
  fi
}

# Test-specific debug logging function
test_debug_log() {
  if [ "$DEBUG" = true ] && [ "$PROFILE_TEST" = true ]; then
    local message="[TEST-DEBUG] $1"
    echo "$message"
    if [ -n "$DEBUG_FILE" ]; then
      echo "$message" >> "$DEBUG_FILE"
    fi
  fi
}

# Timing functions
start_timer() {
  local step_name="$1"
  STEP_TIMES["${step_name}_start"]=$(date +%s.%N)
  debug_log "Starting: $step_name"
}

end_timer() {
  local step_name="$1"
  local end_time=$(date +%s.%N)
  local start_time="${STEP_TIMES["${step_name}_start"]}"
  if [ -n "$start_time" ]; then
    local duration=$(awk "BEGIN {printf \"%.2f\", $end_time - $start_time}")
    STEP_TIMES["${step_name}_duration"]=$duration
    debug_log "Completed: $step_name (${duration}s)"
  fi
}

# Test-specific timing functions
start_test_timer() {
  local step_name="$1"
  STEP_TIMES["${step_name}_start"]=$(date +%s.%N)
  test_debug_log "Starting: $step_name"
}

end_test_timer() {
  local step_name="$1"
  local end_time=$(date +%s.%N)
  local start_time="${STEP_TIMES["${step_name}_start"]}"
  if [ -n "$start_time" ]; then
    local duration=$(awk "BEGIN {printf \"%.2f\", $end_time - $start_time}")
    STEP_TIMES["${step_name}_duration"]=$duration
    test_debug_log "Completed: $step_name (${duration}s)"
  fi
}

print_timing_summary() {
  if [ "$DEBUG" = true ]; then
    local output_lines=()
    
    # Build the output lines
    output_lines+=("")
    output_lines+=("=== TIMING SUMMARY ===")
    local total_time=$(awk "BEGIN {printf \"%.2f\", $(date +%s.%N) - $SCRIPT_START_TIME}")
    output_lines+=("Total script time: ${total_time}s")
    output_lines+=("")
    
    # Sort the timing entries for consistent output
    local sorted_keys=($(printf '%s\n' "${!STEP_TIMES[@]}" | grep '_duration$' | sort))
    
    for key in "${sorted_keys[@]}"; do
      local step_name="${key%_duration}"
      local duration="${STEP_TIMES[$key]}"
      
      # Show test-specific timings only if PROFILE_TEST is enabled
      if [[ "$step_name" == *"opcodes_warmup_"* || "$step_name" == *"test_run_"* ]]; then
        if [ "$PROFILE_TEST" = true ]; then
          output_lines+=("$(printf "%-30s: %8ss" "$step_name" "$duration")")
        fi
      else
        output_lines+=("$(printf "%-30s: %8ss" "$step_name" "$duration")")
      fi
    done
    output_lines+=("=======================")
    output_lines+=("")
    
    # Print to stdout
    printf '%s\n' "${output_lines[@]}"
    
    # Save to file if specified
    if [ -n "$DEBUG_FILE" ]; then
      printf '%s\n' "${output_lines[@]}" >> "$DEBUG_FILE"
    fi
  fi
}

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
  local -a ordered=()
  local -A seen=()
  local file
  local phase
  local order_file="$dir/scenario_order.json"
  local -a scenario_indices=()
  local -a scenario_names=()

  if [ -f "$order_file" ]; then
    while IFS=$'\t' read -r idx name; do
      if [ -n "$name" ]; then
        scenario_indices+=("$idx")
        scenario_names+=("$name")
      fi
    done < <(python3 - <<'PY' "$order_file"
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    sys.exit(0)

if isinstance(data, list):
    for item in data:
        idx = None
        name = None
        if isinstance(item, dict):
            name = item.get("name")
            idx = item.get("index")
        elif isinstance(item, str):
            name = item
        if not name:
            continue
        name = str(name).strip()
        if not name:
            continue
        if isinstance(idx, int):
            print(f"{idx}\t{name}")
        elif isinstance(idx, str) and idx.strip():
            print(f"{idx.strip()}\t{name}")
        else:
            print(f"\t{name}")
PY
)
  fi

  for file in "$dir/gas-bump.txt" "$dir/funding.txt" "$dir/setup-global-test.txt"; do
    if [ -f "$file" ] && [ -z "${seen[$file]}" ]; then
      ordered+=("$file")
      seen["$file"]=1
    fi
  done

  if [ "${#scenario_names[@]}" -gt 0 ]; then
    local scenario_count="${#scenario_names[@]}"
    local si
    for (( si=0; si<scenario_count; si++ )); do
      local scenario="${scenario_names[$si]}"
      local idx="${scenario_indices[$si]}"
      idx="${idx//[[:space:]]/}"
      local idx_dir=""
      if [ -n "$idx" ]; then
        if [[ "$idx" =~ ^[0-9]+$ ]]; then
          printf -v idx_dir "%06d" "$idx"
        else
          idx_dir="$idx"
        fi
      fi
      for phase in setup testing cleanup; do
        local scenario_path
        if [ -n "$idx_dir" ]; then
          scenario_path="$dir/$phase/$idx_dir/$scenario.txt"
        else
          scenario_path="$dir/$phase/$scenario.txt"
        fi
        if [ -f "$scenario_path" ] && [ -z "${seen[$scenario_path]}" ]; then
          ordered+=("$scenario_path")
          seen["$scenario_path"]=1
        fi
      done
    done
  fi

  for phase in setup testing cleanup; do
    local phase_dir="$dir/$phase"
    if [ -d "$phase_dir" ]; then
      while IFS= read -r -d '' phase_file; do
        if [ -z "${seen[$phase_file]}" ]; then
          ordered+=("$phase_file")
          seen["$phase_file"]=1
        fi
      done < <(find "$phase_dir" -type f -name '*.txt' -print0 | sort -z)
    fi
  done

  local teardown="$dir/teardown-global-test.txt"
  if [ -f "$teardown" ] && [ -z "${seen[$teardown]}" ]; then
    ordered+=("$teardown")
    seen["$teardown"]=1
  fi

  if [ -d "$dir" ]; then
    while IFS= read -r -d '' root_file; do
      if [ -z "${seen[$root_file]}" ]; then
        ordered+=("$root_file")
        seen["$root_file"]=1
      fi
    done < <(find "$dir" -maxdepth 1 -type f -name '*.txt' -print0 | sort -z)
  fi

  printf '%s\0' "${ordered[@]}"
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
    echo "⚠️  Compose file not found for $client_base" >&2
    return 1
  fi

  if [ -f "$env_file" ]; then
    if ! docker compose -f "$compose_file" --env-file "$env_file" restart >/dev/null 2>&1; then
      if ! docker compose -f "$compose_file" --env-file "$env_file" restart; then
        echo "❌ Failed to restart services for $client_base" >&2
        return 1
      fi
    fi
  else
    if ! (
      cd "$compose_dir" && docker compose restart
    ); then
      echo "❌ Failed to restart services for $client_base" >&2
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

  echo "⚠️  Test path not found: $base_path" >&2
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
    if [ ! -d "$snapshot_root/$client" ]; then
      if [ -z "$network" ] || [ ! -d "$snapshot_root/$network" ]; then
        lower="$snapshot_root"
      fi
    fi
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
    echo "❌ Unable to locate snapshot directory for $client under $snapshot_root" >&2
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

  local overlay_root="$overlay_base/$client"
  local merged="$overlay_root/merged"
  local upper="$overlay_root/upper"
  local work="$overlay_root/work"

  mkdir -p "$overlay_root"

  if is_mounted "$merged"; then
    if ! umount "$merged" 2>/dev/null; then
      if command -v sudo >/dev/null 2>&1; then
        sudo umount "$merged"
      else
        echo "❌ Failed to unmount previous overlay for $client" >&2
        return 1
      fi
    fi
  fi

  rm -rf "$merged" "$upper" "$work"
  mkdir -p "$merged" "$upper" "$work"

  local abs_upper abs_work
  abs_upper=$(abspath "$upper")
  abs_work=$(abspath "$work")
  local mount_opts="lowerdir=$abs_lower,upperdir=$abs_upper,workdir=$abs_work"

  if ! mount -t overlay overlay -o "$mount_opts" "$merged" 2>/dev/null; then
    if command -v sudo >/dev/null 2>&1; then
      sudo mount -t overlay overlay -o "$mount_opts" "$merged"
    else
      echo "❌ Failed to mount overlay for $client (need elevated permissions)" >&2
      return 1
    fi
  fi

  ACTIVE_OVERLAY_MOUNTS["$client"]="$merged"
  ACTIVE_OVERLAY_UPPERS["$client"]="$upper"
  ACTIVE_OVERLAY_WORKS["$client"]="$work"
  ACTIVE_OVERLAY_CLIENTS["$client"]=1

  echo "$merged"
}

cleanup_overlay_for_client() {
  local client="$1"
  local merged="${ACTIVE_OVERLAY_MOUNTS[$client]}"
  local upper="${ACTIVE_OVERLAY_UPPERS[$client]}"
  local work="${ACTIVE_OVERLAY_WORKS[$client]}"

  if [ -n "$merged" ] && is_mounted "$merged"; then
    if ! umount "$merged" 2>/dev/null; then
      if command -v sudo >/dev/null 2>&1; then
        sudo umount "$merged"
      fi
    fi
  fi

  [ -n "$merged" ] && rm -rf "$merged"
  [ -n "$upper" ] && rm -rf "$upper"
  [ -n "$work" ] && rm -rf "$work"

  unset ACTIVE_OVERLAY_MOUNTS["$client"]
  unset ACTIVE_OVERLAY_UPPERS["$client"]
  unset ACTIVE_OVERLAY_WORKS["$client"]
  unset ACTIVE_OVERLAY_CLIENTS["$client"]
}

cleanup_all_overlays() {
  local client
  for client in "${!ACTIVE_OVERLAY_CLIENTS[@]}"; do
    cleanup_overlay_for_client "$client"
  done
}

docker_compose_down_for_client() {
  local client_base="$1"
  local compose_dir="scripts/$client_base"

  if ! command -v docker >/dev/null 2>&1; then
    return
  fi

  if [ -f "$compose_dir/docker-compose.yaml" ]; then
    docker compose -f "$compose_dir/docker-compose.yaml" down >/dev/null 2>&1 || \
      docker compose -f "$compose_dir/docker-compose.yaml" down
  elif [ -d "$compose_dir" ]; then
    (
      cd "$compose_dir" && docker compose down >/dev/null 2>&1 || docker compose down
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

while getopts "T:t:g:w:c:r:i:o:f:n:B:R:" opt; do
  case $opt in
    T) TEST_PATHS_JSON="$OPTARG" ;;
    t) LEGACY_TEST_PATH="$OPTARG" ;;
    g) LEGACY_GENESIS_PATH="$OPTARG" ;;
    w) WARMUP_FILE="$OPTARG" ;;
    c) CLIENTS="$OPTARG" ;;
    r) RUNS="$OPTARG" ;;
    i) IMAGES="$OPTARG" ;;
    o) OPCODES_WARMUP_COUNT="$OPTARG" ;;
    f) FILTER="$OPTARG" ;;  # comma-separated exclude patterns
    d) DEBUG=true ;;
    D) DEBUG=true; DEBUG_FILE="$OPTARG" ;;
    p) PROFILE_TEST=true ;;
    n) NETWORK="$OPTARG"; USE_OVERLAY=true ;;
    B) SNAPSHOT_ROOT="$OPTARG"; USE_OVERLAY=true ;;
    R) RESTART_BEFORE_TESTING=true;;
    *) echo "Usage: $0 [-t test_path] [-w warmup_file] [-c clients] [-r runs] [-i images] [-o opcodesWarmupCount] [-f filter] [-d debug] [-D debug_file] [-p profile_test] [-n network] [-B snapshot_root]" >&2
       exit 1 ;;
  esac
done

# Fallback to legacy -t/-g if -T not provided
if [ -z "$TEST_PATHS_JSON" ]; then
  if [ -z "$LEGACY_TEST_PATH" ]; then
    echo "❌ You must provide either -T <json> or -t <test_path>"
    exit 1
  fi

  echo "⚠️  Falling back to legacy mode with -t and -g"
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
IFS=',' read -ra FILTERS <<< "$FILTER"

trap cleanup_on_exit EXIT INT TERM

mkdir -p results warmupresults logs

# Initialize debug file if specified
if [ -n "$DEBUG_FILE" ]; then
  # Find next available filename to avoid overwriting
  original_debug_file="$DEBUG_FILE"
  counter=0
  
  while [ -f "$DEBUG_FILE" ]; do
    counter=$((counter + 1))
    # Extract filename and extension
    filename="${original_debug_file%.*}"
    extension="${original_debug_file##*.}"
    
    # Handle files without extension
    if [ "$filename" = "$extension" ]; then
      DEBUG_FILE="${original_debug_file}.${counter}"
    else
      DEBUG_FILE="${filename}.${counter}.${extension}"
    fi
  done
  
  # Create debug file with timestamp header
  echo "=== DEBUG LOG STARTED: $(date) ===" > "$DEBUG_FILE"
  echo "Script: $0" >> "$DEBUG_FILE"
  echo "Args: $*" >> "$DEBUG_FILE"
  echo "=======================================" >> "$DEBUG_FILE"
  
  # Notify user about the actual filename used
  if [ "$DEBUG_FILE" != "$original_debug_file" ]; then
    echo "Debug file '$original_debug_file' already exists, using '$DEBUG_FILE' instead"
  fi
fi

if [ "$USE_OVERLAY" = true ] && [[ "$OVERLAY_TMP_ROOT" = /* ]]; then
  mkdir -p "$OVERLAY_TMP_ROOT"
fi

# Set up environment
start_timer "environment_setup"
rm -rf results
mkdir -p results
mkdir -p warmupresults
mkdir -p logs
rm -rf "$PREPARATION_RESULTS_DIR"
mkdir -p "$PREPARATION_RESULTS_DIR"
end_timer "environment_setup"

# Initialize executions tracking
start_timer "executions_init"
init_executions_file
end_timer "executions_init"

# Install dependencies
start_timer "dependencies_install"
pip install -r requirements.txt
make prepare_tools
end_timer "dependencies_install"

# Find test files and their associated genesis paths
start_timer "test_discovery"
TEST_FILES=()
TEST_TO_GENESIS=()

for i in "${!TEST_PATHS[@]}"; do
  path="${TEST_PATHS[$i]}"
  genesis="${GENESIS_PATHS[$i]}"
  append_tests_for_path "$path" "$genesis"
done
debug_log "Found ${#TEST_FILES[@]} test files"
end_timer "test_discovery"

DEFAULT_GENESIS=""
for genesis_entry in "${TEST_TO_GENESIS[@]}"; do
  if [ -n "$genesis_entry" ]; then
    DEFAULT_GENESIS="$genesis_entry"
    break
  fi
done

# Run benchmarks
start_timer "benchmarks_total"
for run in $(seq 1 $RUNS); do
  debug_log "Starting run $run/$RUNS"
  for client in "${CLIENT_ARRAY[@]}"; do
    debug_log "Processing client: $client"
    
    # Skip nimbus if already run today
    if [ "$client" = "nimbus" ] && was_executed_today "$client"; then
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
        echo "❌ Snapshot root not specified for $client" >&2
        cleanup_overlay_for_client "$client_base"
        continue
      fi
      data_dir=$(prepare_overlay_for_client "$client_base" "$NETWORK" "$snapshot_root_for_client") || {
        echo "❌ Skipping $client - overlay setup failed" >&2
        cleanup_overlay_for_client "$client_base"
        continue
      }
    else
      data_dir=$(abspath "scripts/$client_base/execution-data")
      mkdir -p "$data_dir"
    fi

    end_timer "setup_node_${client}"

    setup_cmd=(python3 setup_node.py --client "$client" --imageBulk "$IMAGES" --dataDir "$data_dir")
    if [ -n "$NETWORK" ]; then
      setup_cmd+=(--network "$NETWORK")
    fi
    if [ -z "$NETWORK" ] && [ -n "$genesis_path" ]; then
      echo "Using custom genesis for $client: $genesis_path"
      setup_cmd+=(--genesisPath "$genesis_path")
    fi

    RUNNING_CLIENTS["$client_base"]=1

    "${setup_cmd[@]}"

    python3 -c "from utils import print_computer_specs; print(print_computer_specs())" > results/computer_specs.txt
    cat results/computer_specs.txt

    warmed=false

    # Warmup
    if [ "$warmed" = false ]; then
      start_timer "warmup_${client}_run_${run}"
      python3 run_kute.py --output warmupresults --testsPath "$WARMUP_FILE" --jwtPath /tmp/jwtsecret --client $client --run $run
      warmed=true
      end_timer "warmup_${client}_run_${run}"
    fi

    declare -A warmup_run_counts=()

    for i in "${!TEST_FILES[@]}"; do
      test_file="${TEST_FILES[$i]}"
      filename="${test_file##*/}"
      if is_measured_file "$test_file"; then
        measured=true
      else
        measured=false
      fi

      if [ "$measured" = true ] && [ -n "$FILTER" ]; then
        match=false
        filename_lc="${filename,,}"  # Convert filename to lowercase once

        for pat in "${FILTERS[@]}"; do
          pat_lc="${pat,,}"  # Convert filter pattern to lowercase

          if [[ "$filename_lc" == *"$pat_lc"* ]]; then
            match=true
            break
          fi
        done

        if [ "$match" != true ]; then
          echo "Skipping $filename (does not match case-insensitive filter)"
          continue
        fi
      fi

      if [ "$measured" = false ]; then
        echo "Executing preparation script (not measured): $filename"
        python3 run_kute.py --output "$PREPARATION_RESULTS_DIR" --testsPath "$test_file" --jwtPath /tmp/jwtsecret --client $client --run $run
        echo ""
        continue
      fi

      if [ "$RESTART_BEFORE_TESTING" = true ]; then
        if ! restart_client_containers "$client_base"; then
          echo "⚠️  Skipping $filename for $client - restart failed" >&2
          continue
        fi
      fi

      base_prefix="${filename%-gas-value_*}"
      warmup_candidates=( "$WARMUP_OPCODES_PATH"/"$base_prefix"-gas-value_*.txt )
      warmup_path="${warmup_candidates[0]}"

      if (( OPCODES_WARMUP_COUNT > 0 )); then
        start_test_timer "opcodes_warmup_${client}_${filename}"
        current_count="${warmup_run_counts[$warmup_path]:-0}"
        if (( current_count >= OPCODES_WARMUP_COUNT )); then
          echo ""
        else
          for warmup_count in $(seq 1 $OPCODES_WARMUP_COUNT); do
            test_debug_log "Opcodes warmup $warmup_count/$OPCODES_WARMUP_COUNT for $filename"
            python3 run_kute.py --output warmupresults --testsPath "$warmup_path" --jwtPath /tmp/jwtsecret --client $client --run $run --kuteArguments '-f engine_newPayload'
            warmup_run_counts["$warmup_path"]=$((warmup_run_counts["$warmup_path"] + 1))
          done
          end_test_timer "opcodes_warmup_${client}_${filename}"
        fi
      fi

      # Actual measured run
      start_test_timer "test_run_${client}_${filename}"
      test_debug_log "Running test: $filename"
      python3 run_kute.py --output results --testsPath "$test_file" --jwtPath /tmp/jwtsecret --client $client --run $run
      end_test_timer "test_run_${client}_${filename}"
      echo "" # Line break after each test for logs clarity
    done

    # Collect logs & teardown
    start_timer "teardown_${client}"
    ts=$(date +%s)
    dump_client_logs "$client_base"
    docker_compose_down_for_client "$client_base"

    rm -rf "scripts/$client_base/execution-data"

    if [ "$USE_OVERLAY" = true ]; then
      cleanup_overlay_for_client "$client_base"
    fi
    end_timer "teardown_${client}"

    unset RUNNING_CLIENTS["$client_base"]

    update_execution_time "$client"
    end_timer "client_${client}_run_${run}"
  done
done
end_timer "benchmarks_total"

start_timer "results_processing"
if [ -z "$IMAGES" ]; then
  python3 report_tables.py --resultsPath results --clients "$CLIENTS" --testsPath "${TEST_PATHS[0]}" --runs "$RUNS"
  python3 report_html.py   --resultsPath results --clients "$CLIENTS" --testsPath "${TEST_PATHS[0]}" --runs "$RUNS"
else
  python3 report_tables.py --resultsPath results --clients "$CLIENTS" --testsPath "${TEST_PATHS[0]}" --runs "$RUNS" --images "$IMAGES"
  python3 report_html.py   --resultsPath results --clients "$CLIENTS" --testsPath "${TEST_PATHS[0]}" --runs "$RUNS" --images "$IMAGES"
fi
end_timer "results_processing"

# Prepare and zip the results
start_timer "results_packaging"
mkdir -p reports/docker
cp -r results/docker_* reports/docker
zip -r reports.zip reports
end_timer "results_packaging"

# Print timing summary at the end
print_timing_summary
