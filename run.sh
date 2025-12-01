#!/bin/bash

# Default inputs
WARMUP_OPCODES_PATH="warmup-tests"
WARMUP_FILE="warmup/warmup-1000bl-16wi-24tx.txt"
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
SKIP_FORKCHOICE=false
PER_TEST_RESTARTS=false

if [ -f "scripts/common/wait_for_rpc.sh" ]; then
  # shellcheck source=/dev/null
  source "scripts/common/wait_for_rpc.sh"
fi

# Timing variables
declare -A STEP_TIMES
declare -A ACTIVE_OVERLAY_MOUNTS
declare -A ACTIVE_OVERLAY_UPPERS
declare -A ACTIVE_OVERLAY_WORKS
declare -A ACTIVE_OVERLAY_ROOTS
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

sanitize_path_component() {
  local input="$1"
  # Replace any characters outside a safe set with underscores
  echo "$input" | tr -cs '[:alnum:]._-' '_'
}

safe_remove_dir() {
  local target="$1"
  if [ -z "$target" ]; then
    return
  fi
  rm -rf "$target" 2>/dev/null || true
  if [ -d "$target" ]; then
    if command -v sudo >/dev/null 2>&1; then
      sudo rm -rf "$target" 2>/dev/null || sudo rm -rf "$target"
    fi
  fi
}

teardown_client_instance() {
  local timer_label="$1"
  if [ -n "$timer_label" ]; then
    start_timer "$timer_label"
  fi
  dump_client_logs "$client_base"
  docker_compose_down_for_client "$client_base"
  unset RUNNING_CLIENTS["$client_base"]
  if [ "$USE_OVERLAY" != true ]; then
    safe_remove_dir "$data_dir"
  fi
  if drop_host_caches; then
    debug_log "Dropped host caches"
  else
    debug_log "Skipped host cache drop (insufficient permissions)"
  fi
  if [ -n "$timer_label" ]; then
    end_timer "$timer_label"
  fi
}

launch_client_instance() {
  local artifacts_dir="$1"

  local volume_name="${client_base}_$(date +%s)_$RANDOM"
  if [ "$USE_OVERLAY" = true ]; then
    local overlay_root="${ACTIVE_OVERLAY_ROOTS[$client_base]}"
    if [ -n "$overlay_root" ]; then
      local overlay_token
      overlay_token=$(basename "$overlay_root")
      volume_name="${client_base}_${overlay_token}_$(date +%s)_$RANDOM"
    fi
  fi
  volume_name=$(echo "$volume_name" | tr -cd '[:alnum:]._-')
  if [ -z "$volume_name" ]; then
    volume_name="${client_base}_volume"
  fi

  local setup_cmd=(python3 setup_node.py --client "$client" --imageBulk "$IMAGES" --dataDir "$data_dir")
  if [ -n "$NETWORK" ]; then
    setup_cmd+=(--network "$NETWORK")
  fi
  if [ -z "$NETWORK" ] && [ -n "$genesis_path" ]; then
    echo "Using custom genesis for $client: $genesis_path"
    setup_cmd+=(--genesisPath "$genesis_path")
  fi
  setup_cmd+=(--volumeName "$volume_name")

  echo "[INFO] Running setup_node command: ${setup_cmd[*]}"
  if ! CLIENT_ARTIFACTS_DIR="$artifacts_dir" "${setup_cmd[@]}"; then
    return 1
  fi

  if declare -f wait_for_rpc >/dev/null 2>&1; then
    if ! wait_for_rpc "http://127.0.0.1:8545" 300; then
      return 1
    fi
  else
    sleep 5
  fi

  if [ "$computer_specs_written" = false ]; then
    python3 -c "from utils import print_computer_specs; print(print_computer_specs())" > results/computer_specs.txt
    cat results/computer_specs.txt
    computer_specs_written=true
  fi

  RUNNING_CLIENTS["$client_base"]=1
  return 0
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

sys.stdout.write("\0".join(final))
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

while getopts "T:t:g:w:c:r:i:o:f:n:B:R:FX" opt; do
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
    F) SKIP_FORKCHOICE=true;;
    X) PER_TEST_RESTARTS=true;;
    *) echo "Usage: $0 [-t test_path] [-w warmup_file] [-c clients] [-r runs] [-i images] [-o opcodesWarmupCount] [-f filter] [-d debug] [-D debug_file] [-p profile_test] [-n network] [-B snapshot_root] [-F skipForkchoice] [-X per_test_restarts]" >&2
       exit 1 ;;
  esac
done



# Fallback to legacy -t/-g if -T not provided
if [ -z "$TEST_PATHS_JSON" ]; then
  if [ -z "$LEGACY_TEST_PATH" ]; then
    echo "âťŚ You must provide either -T <json> or -t <test_path>"
    exit 1
  fi

  echo "âš ď¸Ź  Falling back to legacy mode with -t and -g"
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

BASE_ARTIFACTS_ROOT="${CLIENT_ARTIFACTS_DIR:-}"
if [ -n "$BASE_ARTIFACTS_ROOT" ]; then
  BASE_ARTIFACTS_ROOT=$(abspath "$BASE_ARTIFACTS_ROOT")
fi
computer_specs_written=false

# Run benchmarks
start_timer "benchmarks_total"
for run in $(seq 1 $RUNS); do
  debug_log "Starting run $run/$RUNS"
  for client in "${CLIENT_ARRAY[@]}"; do
    debug_log "Processing client: $client"
    
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
    client_artifacts_root="$BASE_ARTIFACTS_ROOT"
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
    if [ -z "$client_artifacts_root" ]; then
      client_artifacts_root=$(abspath "scripts/$client_base/artifacts")
    fi
    mkdir -p "$client_artifacts_root"

    if [ "$PER_TEST_RESTARTS" != true ]; then
      run_artifacts_dir="$client_artifacts_root/run_${run}"
      rm -rf "$run_artifacts_dir"
      mkdir -p "$run_artifacts_dir"
      if [ "$USE_OVERLAY" != true ]; then
        safe_remove_dir "$data_dir"
        mkdir -p "$data_dir"
      fi
      if ! launch_client_instance "$run_artifacts_dir"; then
        echo "⚠️  Failed to start $client for run $run; skipping client." >&2
        teardown_client_instance ""
        if [ "$USE_OVERLAY" = true ]; then
          cleanup_overlay_for_client "$client_base"
          cleanup_stale_overlay_mounts
        fi
        continue
      fi
    fi

    declare -A warmup_run_counts=()
    current_scenario=""
    scenario_active=false
    scenario_failed=false

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

      scenario_base_name="${filename%.*}"
      scenario_safe_name=$(sanitize_path_component "$scenario_base_name")
      if [ -z "$scenario_safe_name" ]; then
        scenario_safe_name="scenario_${i}"
      fi

      if [ "$PER_TEST_RESTARTS" = true ]; then
        if [ "$scenario_safe_name" != "$current_scenario" ]; then
          if [ "$scenario_active" = true ]; then
            teardown_client_instance "teardown_${client}_${current_scenario}_run_${run}"
            scenario_active=false
          fi
          current_scenario="$scenario_safe_name"
          scenario_failed=false

          scenario_artifacts_dir="$client_artifacts_root/run_${run}_${scenario_safe_name}"
          rm -rf "$scenario_artifacts_dir"
          mkdir -p "$scenario_artifacts_dir"

          if [ "$USE_OVERLAY" != true ]; then
            safe_remove_dir "$data_dir"
            mkdir -p "$data_dir"
          fi

          if ! launch_client_instance "$scenario_artifacts_dir"; then
            echo "⚠️  Failed to start $client for scenario $filename; skipping scenario." >&2
            teardown_client_instance ""
            scenario_active=false
            scenario_failed=true
            continue
          fi
          scenario_active=true
        elif [ "$scenario_failed" = true ]; then
          continue
        fi
      fi

      if [ "$measured" = false ]; then
        echo "Executing preparation script (not measured): $filename"
        echo "[INFO] Running preparation run_kute command: python3 run_kute.py --output \"$PREPARATION_RESULTS_DIR\" --testsPath \"$test_file\" --jwtPath /tmp/jwtsecret --client $client --run $run$SKIP_FORKCHOICE_OPT"
        python3 run_kute.py --output "$PREPARATION_RESULTS_DIR" --testsPath "$test_file" --jwtPath /tmp/jwtsecret --client $client --run $run$SKIP_FORKCHOICE_OPT
        echo ""

        continue
      fi

      if [ -n "$WARMUP_FILE" ]; then
        start_test_timer "warmup_${client}_${scenario_safe_name}_run_${run}"
        if [ -f "$WARMUP_FILE" ]; then
          echo "[INFO] Running warmup run_kute command: python3 run_kute.py --output warmupresults --testsPath \"$WARMUP_FILE\" --jwtPath /tmp/jwtsecret --client $client --run $run$SKIP_FORKCHOICE_OPT"
          python3 run_kute.py --output warmupresults --testsPath "$WARMUP_FILE" --jwtPath /tmp/jwtsecret --client $client --run $run$SKIP_FORKCHOICE_OPT
        else
          echo "[WARN] Warmup file '$WARMUP_FILE' not found; skipping warmup."
        fi
        end_test_timer "warmup_${client}_${scenario_safe_name}_run_${run}"
      fi

      base_prefix="${filename%-gas-value_*}"
      warmup_candidates=( "$WARMUP_OPCODES_PATH"/"$base_prefix"-gas-value_*.txt )
      warmup_path="${warmup_candidates[0]}"

      if (( OPCODES_WARMUP_COUNT > 0 )); then
        if [ -f "$warmup_path" ]; then
          start_test_timer "opcodes_warmup_${client}_${filename}"
          current_count="${warmup_run_counts[$warmup_path]:-0}"
          if (( current_count >= OPCODES_WARMUP_COUNT )); then
            echo ""
          else
            for warmup_count in $(seq 1 $OPCODES_WARMUP_COUNT); do
              test_debug_log "Opcodes warmup $warmup_count/$OPCODES_WARMUP_COUNT for $filename"
              echo "[INFO] Running opcode warmup run_kute command: python3 run_kute.py --output warmupresults --testsPath \"$warmup_path\" --jwtPath /tmp/jwtsecret --client $client --run $run --kuteArguments '-f engine_newPayload'$SKIP_FORKCHOICE_OPT"
              python3 run_kute.py --output warmupresults --testsPath "$warmup_path" --jwtPath /tmp/jwtsecret --client $client --run $run --kuteArguments '-f engine_newPayload'$SKIP_FORKCHOICE_OPT
              warmup_run_counts["$warmup_path"]=$((warmup_run_counts["$warmup_path"] + 1))
            done
            end_test_timer "opcodes_warmup_${client}_${filename}"
          fi
        else
          test_debug_log "No opcode warmup file found for prefix $base_prefix (expected $warmup_path)"
        fi
      fi

      # Actual measured run
      if drop_host_caches; then
        test_debug_log "Dropped host caches before scenario $filename"
      else
        test_debug_log "Skipped host cache drop before scenario $filename (insufficient permissions)"
      fi
      start_test_timer "test_run_${client}_${filename}"
      test_debug_log "Running test: $filename"
      echo "[INFO] Running measured run_kute command: python3 run_kute.py --output results --testsPath \"$test_file\" --jwtPath /tmp/jwtsecret --client $client --run $run$SKIP_FORKCHOICE_OPT"
      python3 run_kute.py --output results --testsPath "$test_file" --jwtPath /tmp/jwtsecret --client $client --run $run$SKIP_FORKCHOICE_OPT
      end_test_timer "test_run_${client}_${filename}"
      echo "" # Line break after each test for logs clarity

      if drop_host_caches; then
        debug_log "Dropped host caches"
      else
        debug_log "Skipped host cache drop (insufficient permissions)"
      fi
    done

    if [ "$PER_TEST_RESTARTS" = true ]; then
      if [ "$scenario_active" = true ]; then
        teardown_client_instance "teardown_${client}_${current_scenario}_run_${run}"
        scenario_active=false
      fi
      current_scenario=""
    else
      teardown_client_instance "teardown_${client}_run_${run}"
    fi

    if [ "$USE_OVERLAY" = true ]; then
      cleanup_overlay_for_client "$client_base"
      cleanup_stale_overlay_mounts
    fi

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
