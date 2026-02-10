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
GENERATE_RESPONSE_HASHES=false
HASH_CAPTURE_MODE="request"
HASH_OUTPUT_DIR="response_hashes"
HASH_CAPTURE_LOG_DIR="mitmproxy_logs"
ENABLE_MITMPROXY_LOGGING=false
HASH_CAPTURE_MITM_PID=""
CURRENT_TEST_NAME_FILE="/tmp/current_test_name.txt"

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

hash_json_file() {
  local file="$1"
  python3 - "$file" <<'PY'
import json, sys, hashlib
from pathlib import Path

def normalize(value):
    if isinstance(value, dict):
        return {k: normalize(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value

path = Path(sys.argv[1])
fragments = []
if path.exists():
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                fragments.append(line)
                continue
            normalized = normalize(data)
            fragments.append(json.dumps(normalized, separators=(",", ":"), sort_keys=True))

payload = "\n".join(fragments).encode("utf-8")
print(hashlib.sha256(payload).hexdigest())
PY
}

validate_cross_client_results() {
  local -a requested_clients=()
  local client
  for client in "${CLIENT_ARRAY[@]}"; do
    if [ -n "$client" ]; then
      requested_clients+=("$client")
    fi
  done

  if [ "${#requested_clients[@]}" -le 1 ]; then
    return 0
  fi

  shopt -s nullglob
  local -a result_files=(results/*_response_*.txt)
  shopt -u nullglob

  if [ "${#result_files[@]}" -eq 0 ]; then
    echo "[WARN] Cross-client validation skipped: no result files found."
    return 0
  fi

  local -A scenario_hashes=()
  local -A scenario_clients=()
  local -A scenario_baseline_file=()
  local -A scenario_baseline_client=()
  local -A clients_seen=()
  local -a mismatches=()
  local -a missing=()
  local file filename scenario hash

  for file in "${result_files[@]}"; do
    filename=$(basename "$file")
    scenario=${filename#*_response_}
    client=${filename%%_response_*}
    hash=$(hash_json_file "$file")

    clients_seen["$client"]=1
    scenario_clients["$scenario"]="${scenario_clients[$scenario]} $client"
    if [ -z "${scenario_hashes[$scenario]}" ]; then
      scenario_hashes["$scenario"]="$hash"
      scenario_baseline_file["$scenario"]="$file"
      scenario_baseline_client["$scenario"]="$client"
    elif [ "${scenario_hashes[$scenario]}" != "$hash" ]; then
      mismatches+=("$scenario|${scenario_baseline_client[$scenario]}|$client|${scenario_baseline_file[$scenario]}|$file")
    fi
  done

  local -a active_clients=()
  for client in "${!clients_seen[@]}"; do
    active_clients+=("$client")
  done

  if [ "${#active_clients[@]}" -le 1 ]; then
    if [ "${#active_clients[@]}" -eq 0 ]; then
      echo "[WARN] Cross-client validation skipped: no result files were produced."
    else
      echo "[WARN] Cross-client validation skipped: only client '${active_clients[0]}' produced results."
    fi
    for client in "${requested_clients[@]}"; do
      if [ -z "${clients_seen[$client]}" ]; then
        echo "[WARN] No result files found for requested client '$client'."
      fi
    done
    return 0
  fi

  local scenario scenario_clients_str missing_found=false
  for scenario in "${!scenario_hashes[@]}"; do
    scenario_clients_str=" ${scenario_clients[$scenario]} "
    for client in "${active_clients[@]}"; do
      if [[ "$scenario_clients_str" != *" $client "* ]]; then
        missing+=("$scenario|$client")
        missing_found=true
      fi
    done
  done

  # Log missing results as warnings
  if [ "$missing_found" = true ]; then
    echo "[WARN] Some clients are missing results for certain scenarios (skipping those scenarios in validation):"
    for entry in "${missing[@]}"; do
      IFS='|' read -r scenario client <<< "$entry"
      echo "  [WARN] Missing results for client '$client' in scenario '$scenario'"
    done
  fi

  # Log hash mismatches as warnings
  if [ "${#mismatches[@]}" -gt 0 ]; then
    echo "[WARN] Hash mismatches detected between clients:"
    for entry in "${mismatches[@]}"; do
      IFS='|' read -r scenario base_client client base_file current_file <<< "$entry"
      echo "  [WARN] Mismatch detected for scenario '$scenario' between '$base_client' and '$client'"
      if command -v diff >/dev/null 2>&1; then
        diff -u "$base_file" "$current_file" | sed 's/^/    /'
      fi
    done
  fi

  echo "[INFO] Cross-client validation completed across ${#active_clients[@]} clients: ${active_clients[*]}"
  return 0
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
  python3 - <<'PY' "$dir"
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

phase_to_files = {}
for phase in ("setup", "testing", "cleanup"):
    phase_dir = root / phase
    per_name = {}
    if phase_dir.is_dir():
        for file in sorted(phase_dir.rglob("*.txt")):
            # If multiple files with the same stem exist (legacy leftovers),
            # keep a deterministic choice.
            existing = per_name.get(file.stem)
            if existing is None or str(file) < str(existing):
                per_name[file.stem] = file
    phase_to_files[phase] = per_name

scenario_names = sorted(
    set(phase_to_files["setup"].keys())
    | set(phase_to_files["testing"].keys())
    | set(phase_to_files["cleanup"].keys())
)

for name in scenario_names:
    for phase in ("setup", "testing", "cleanup"):
        path = phase_to_files[phase].get(name)
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

# Hash capture proxy functions for response hashing
start_hash_capture_proxy() {
  local client="$1"
  local run="$2"

  if [ "$GENERATE_RESPONSE_HASHES" != true ]; then
    return 0
  fi

  # Create config JSON for the mitmproxy addon
  local config_json
  # Build config JSON - only include log_dir if logging is enabled
  if [ "$ENABLE_MITMPROXY_LOGGING" = true ]; then
    config_json=$(jq -n \
      --arg client "$client" \
      --argjson run "$run" \
      --arg output_dir "$HASH_OUTPUT_DIR" \
      --arg mode "$HASH_CAPTURE_MODE" \
      --arg log_dir "$HASH_CAPTURE_LOG_DIR" \
      '{client: $client, run: $run, output_dir: $output_dir, mode: $mode, log_dir: $log_dir}')
    mkdir -p "$HASH_CAPTURE_LOG_DIR"
  else
    config_json=$(jq -n \
      --arg client "$client" \
      --argjson run "$run" \
      --arg output_dir "$HASH_OUTPUT_DIR" \
      --arg mode "$HASH_CAPTURE_MODE" \
      '{client: $client, run: $run, output_dir: $output_dir, mode: $mode}')
  fi

  # Create output directory
  mkdir -p "$HASH_OUTPUT_DIR"

  # Start mitmproxy in reverse proxy mode
  echo "[INFO] Starting hash capture proxy for $client run $run (mode: $HASH_CAPTURE_MODE)..."
  HASH_CAPTURE_CONFIG="$config_json" mitmdump -q -p 8552 --mode reverse:http://127.0.0.1:8551 -s hash_capture_addon.py &
  HASH_CAPTURE_MITM_PID=$!

  # Give the proxy time to start
  sleep 1

  if ! kill -0 "$HASH_CAPTURE_MITM_PID" 2>/dev/null; then
    echo "[ERROR] Hash capture proxy failed to start"
    HASH_CAPTURE_MITM_PID=""
    return 1
  fi

  echo "[INFO] Hash capture proxy started (PID: $HASH_CAPTURE_MITM_PID)"
  return 0
}

stop_hash_capture_proxy() {
  if [ -n "$HASH_CAPTURE_MITM_PID" ]; then
    echo "[INFO] Stopping hash capture proxy (PID: $HASH_CAPTURE_MITM_PID)..."
    kill "$HASH_CAPTURE_MITM_PID" 2>/dev/null || true
    wait "$HASH_CAPTURE_MITM_PID" 2>/dev/null || true
    HASH_CAPTURE_MITM_PID=""
  fi
}

write_current_test_name() {
  local test_name="$1"
  echo "$test_name" > "$CURRENT_TEST_NAME_FILE"
}

clear_current_test_name() {
  rm -f "$CURRENT_TEST_NAME_FILE"
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

overlay_base_from_lower() {
  local abs_lower="$1"
  if [[ "$OVERLAY_TMP_ROOT" = /* ]]; then
    echo "$OVERLAY_TMP_ROOT"
    return
  fi
  local lower_parent
  lower_parent=$(dirname "$abs_lower")
  echo "$lower_parent/$OVERLAY_TMP_ROOT"
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

  local abs_lower
  abs_lower=$(abspath "$lower")
  local overlay_base
  overlay_base=$(overlay_base_from_lower "$abs_lower")

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
  local base="$1"
  if [ -z "$base" ]; then
    base="$OVERLAY_TMP_ROOT"
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

collect_overlay_bases() {
  local bases=()
  if [ -z "$OVERLAY_TMP_ROOT" ]; then
    return
  fi

  if [[ "$OVERLAY_TMP_ROOT" = /* ]]; then
    bases+=("$OVERLAY_TMP_ROOT")
  else
    if [ "${#CLIENT_ARRAY[@]}" -gt 0 ]; then
      local client_spec client_base snapshot_root_for_client abs_snapshot parent
      for client_spec in "${CLIENT_ARRAY[@]}"; do
        if [ -z "$client_spec" ]; then
          continue
        fi
        client_base=$(echo "$client_spec" | cut -d '_' -f 1)
        snapshot_root_for_client=$(resolve_snapshot_root_for_client "$client_base" "$NETWORK")
        if [ -z "$snapshot_root_for_client" ]; then
          continue
        fi
        abs_snapshot=$(abspath "$snapshot_root_for_client")
        parent=$(dirname "$abs_snapshot")
        bases+=("$parent/$OVERLAY_TMP_ROOT")
      done
    fi
    if [ "${#bases[@]}" -eq 0 ]; then
      bases+=("$(abspath "$OVERLAY_TMP_ROOT")")
    fi
  fi

  local b
  declare -A seen
  for b in "${bases[@]}"; do
    if [ -n "$b" ] && [ -z "${seen[$b]}" ]; then
      seen[$b]=1
      echo "$b"
    fi
  done
}

cleanup_all_stale_overlay_mounts() {
  local base
  while IFS= read -r base; do
    cleanup_stale_overlay_mounts "$base"
  done < <(collect_overlay_bases)
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

  # Stop hash capture proxy if running
  stop_hash_capture_proxy
  clear_current_test_name

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

  if declare -F cleanup_all_stale_overlay_mounts >/dev/null 2>&1; then
    cleanup_all_stale_overlay_mounts
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

while getopts "T:t:g:w:c:r:i:o:f:n:B:R:FWSH:L" opt; do
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
    O) OVERLAY_TMP_ROOT="$OPTARG"; USE_OVERLAY=true ;;
    R) RESTART_BEFORE_TESTING=true;;
    F) SKIP_FORKCHOICE=true;;
    S) SKIP_EMPTY=true;;
    H) GENERATE_RESPONSE_HASHES=true; HASH_CAPTURE_MODE="$OPTARG" ;;
    L) ENABLE_MITMPROXY_LOGGING=true ;;
    W) WARMUP_OPCODES_PATH="$OPTARG" ;;
    *) echo "Usage: $0 [-t test_path] [-w warmup_file] [-c clients] [-r runs] [-i images] [-o opcodesWarmupCount] [-f filter] [-d debug] [-D debug_file] [-p profile_test] [-n network] [-B snapshot_root] [-F skipForkchoice] [-W warmup_opcodes_path] [-S skipEmpty] [-H mode (request|response|all)] [-L enable mitmproxy logging]" >&2
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
  cleanup_all_stale_overlay_mounts
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

    # Start hash capture proxy if enabled
    HASH_EC_URL_ARG=""
    if [ "$GENERATE_RESPONSE_HASHES" = true ]; then
      if start_hash_capture_proxy "$client" "$run"; then
        HASH_EC_URL_ARG=" --ecURL http://localhost:8552"
      else
        echo "[WARN] Hash capture proxy failed to start, proceeding without response hashing"
      fi
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
      start_test_timer "test_run_${client}_${filename}"
      test_debug_log "Running test: $filename"
      # Write test name for hash capture addon
      if [ "$GENERATE_RESPONSE_HASHES" = true ]; then
        write_current_test_name "$filename"
      fi
      echo "[INFO] Running measured run_kute command: python3 run_kute.py --output results --testsPath \"$test_file\" --jwtPath /tmp/jwtsecret --client $client --run $run$SKIP_FORKCHOICE_OPT$HASH_EC_URL_ARG"
      python3 run_kute.py --output results --testsPath "$test_file" --jwtPath /tmp/jwtsecret --client $client --run $run$SKIP_FORKCHOICE_OPT$HASH_EC_URL_ARG
      end_test_timer "test_run_${client}_${filename}"

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

    # Stop hash capture proxy if it was started for this client
    stop_hash_capture_proxy

    # Collect logs & teardown
    ts=$(date +%s)
    dump_client_logs "$client_base"
    docker_compose_down_for_client "$client_base"

    rm -rf "scripts/$client_base/execution-data"

    if [ "$USE_OVERLAY" = true ]; then
      cleanup_overlay_for_client "$client_base"
      cleanup_all_stale_overlay_mounts
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

start_timer "cross_client_validation"
validate_cross_client_results
end_timer "cross_client_validation"

# Prepare and zip the results
mkdir -p reports/docker
cp -r results/docker_* reports/docker
zip -r reports.zip reports

# Print timing summary at the end
