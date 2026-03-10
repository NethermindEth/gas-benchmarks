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
USE_SNAPSHOT_BACKEND=false
SNAPSHOT_BACKEND="overlay"
ZFS_RUNTIME_DATASET_ROOT="gasbench-runtime"
ZFS_SNAPSHOT_PREFIX="gasbench_tmp"
PREPARATION_RESULTS_DIR="prepresults"
RESTART_BEFORE_TESTING=false
SKIP_FORKCHOICE=false
SKIP_EMPTY=true
RPC_READINESS_MAX_ATTEMPTS="${RPC_READINESS_MAX_ATTEMPTS:-50}"
OVERLAY_MOUNT_EXTRA_OPTS="${OVERLAY_MOUNT_EXTRA_OPTS:-}"
OVERLAY_USE_VOLATILE="${OVERLAY_USE_VOLATILE:-false}"

# Prevent inherited low API pin from older docker clients/wrappers.
unset DOCKER_API_VERSION

if [ -f "scripts/common/wait_for_rpc.sh" ]; then
  # shellcheck source=/dev/null
  source "scripts/common/wait_for_rpc.sh"
fi
if [ -f "scripts/common/docker_compose.sh" ]; then
  # shellcheck source=/dev/null
  source "scripts/common/docker_compose.sh"
fi

if ! declare -f compose_cmd >/dev/null 2>&1; then
  compose_cmd() { docker compose "$@"; }
fi
if ! declare -f docker_cmd >/dev/null 2>&1; then
  docker_cmd() { docker "$@"; }
fi
if ! declare -f resolve_docker_bin >/dev/null 2>&1; then
  resolve_docker_bin() { command -v docker 2>/dev/null || true; }
fi
if ! declare -f compose_detect >/dev/null 2>&1; then
  compose_detect() { command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; }
fi

declare -A ACTIVE_OVERLAY_MOUNTS
declare -A ACTIVE_OVERLAY_UPPERS
declare -A ACTIVE_OVERLAY_WORKS
declare -A ACTIVE_OVERLAY_ROOTS
declare -A ACTIVE_OVERLAY_CLIENTS
declare -A ACTIVE_ZFS_DATASETS
declare -A ACTIVE_ZFS_SNAPSHOTS
declare -A ACTIVE_ZFS_MOUNTS
declare -A ACTIVE_ZFS_CLIENTS
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
  local original_template="$SNAPSHOT_ROOT"

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

  # Enforce per-network snapshot selection even if caller omitted <<NETWORK>>.
  if [ -n "$network_lower" ] && [[ "$original_template" != *"<<NETWORK>>"* ]] && [[ "$original_template" != *"<<network>>"* ]] && [[ "$original_template" != *"<<Network>>"* ]]; then
    root_template="${root_template%/}/$network_lower"
  fi

  echo "$root_template"
}

restart_client_containers() {
  local client_base="$1"
  local compose_dir="scripts/$client_base"
  local compose_file="$compose_dir/docker-compose.yaml"
  local env_file="$compose_dir/.env"

  if [ ! -f "$compose_file" ]; then
    echo "[WARN] Compose file not found for $client_base" >&2
    return 1
  fi

  if [ -f "$env_file" ]; then
    if ! compose_cmd -f "$compose_file" --env-file "$env_file" restart >/dev/null 2>&1; then
      if ! compose_cmd -f "$compose_file" --env-file "$env_file" restart; then
        echo "[ERROR] Failed to restart services for $client_base" >&2
        return 1
      fi
    fi
  else
    if ! (
      cd "$compose_dir" && compose_cmd restart
    ); then
      echo "[ERROR] Failed to restart services for $client_base" >&2
      return 1
    fi
  fi

  if declare -f wait_for_rpc >/dev/null 2>&1; then
    wait_for_rpc "http://127.0.0.1:8545" "$RPC_READINESS_MAX_ATTEMPTS"
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

# ---------------------------------------------------------------------------
# Bootstrap FCU – send a forkchoiceUpdated to drive the client to the
# expected head block via p2p.  Used when a snapshot is a few blocks behind.
# Args: $1 = head_block_hash, $2 = max_retries (default 60),
#        $3 = backoff_seconds (default 30)
# ---------------------------------------------------------------------------
bootstrap_fcu() {
  local head_block_hash="$1"
  local max_retries="${2:-60}"
  local backoff="${3:-30}"
  local jwt_secret_path="/tmp/jwtsecret"
  local engine_url="http://127.0.0.1:8551"

  echo "[INFO] Bootstrap FCU: driving head to $head_block_hash (max_retries=$max_retries, backoff=${backoff}s)"

  python3 - "$head_block_hash" "$max_retries" "$backoff" "$jwt_secret_path" "$engine_url" <<'PYEOF'
import sys, time, json, urllib.request, urllib.error, hmac, hashlib, base64, math

head_hash    = sys.argv[1]
max_retries  = int(sys.argv[2])
backoff_secs = int(sys.argv[3])
jwt_path     = sys.argv[4]
engine_url   = sys.argv[5]

with open(jwt_path) as f:
    jwt_secret = bytes.fromhex(f.read().strip())

def make_jwt():
    header  = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b'=')
    payload = base64.urlsafe_b64encode(
        json.dumps({"iat": int(time.time())}).encode()
    ).rstrip(b'=')
    msg = header + b'.' + payload
    sig = base64.urlsafe_b64encode(
        hmac.new(jwt_secret, msg, hashlib.sha256).digest()
    ).rstrip(b'=')
    return (msg + b'.' + sig).decode()

fcu_payload = json.dumps({
    "jsonrpc": "2.0",
    "method": "engine_forkchoiceUpdatedV3",
    "params": [
        {
            "headBlockHash": head_hash,
            "safeBlockHash": head_hash,
            "finalizedBlockHash": head_hash,
        },
        None,
    ],
    "id": 1,
}).encode()

for attempt in range(1, max_retries + 1):
    try:
        token = make_jwt()
        req = urllib.request.Request(
            engine_url,
            data=fcu_payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        status = (body.get("result") or {}).get("payloadStatus", {}).get("status", "")
        print(f"[INFO] Bootstrap FCU attempt {attempt}/{max_retries}: status={status}", flush=True)
        if status == "VALID":
            print("[INFO] Bootstrap FCU succeeded – head is VALID", flush=True)
            sys.exit(0)
    except Exception as exc:
        print(f"[WARN] Bootstrap FCU attempt {attempt}/{max_retries} failed: {exc}", flush=True)

    if attempt < max_retries:
        time.sleep(backoff_secs)

print("[ERROR] Bootstrap FCU exhausted all retries", file=sys.stderr, flush=True)
sys.exit(1)
PYEOF
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

  echo "[WARN] Test path not found: $base_path" >&2
}

is_mounted() {
  local mount_point="$1"
  local abs_path
  abs_path=$(abspath "$mount_point")
  grep -q " $abs_path " /proc/mounts 2>/dev/null
}

resolve_snapshot_lower_for_client() {
  local client="$1"
  local network="$2"
  local snapshot_root="$3"

  local lower=""

  if [ -n "$network" ] && dir_has_content "$snapshot_root/$network/$client"; then
    lower="$snapshot_root/$network/$client"
  fi

  if [ -z "$lower" ] && [ -n "$network" ] && dir_has_content "$snapshot_root/$network" && [ ! -d "$snapshot_root/$network/$client" ]; then
    lower="$snapshot_root/$network"
  fi

  if [ -z "$lower" ] && dir_has_content "$snapshot_root/$client"; then
    lower="$snapshot_root/$client"
  fi

  if [ -z "$lower" ] && dir_has_content "$snapshot_root"; then
    lower="$snapshot_root"
  fi

  if [ -z "$lower" ]; then
    echo "Unable to locate snapshot directory for $client under $snapshot_root" >&2
    return 1
  fi

  echo "$lower"
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

run_zfs() {
  if zfs "$@" 2>/dev/null; then
    return 0
  fi

  if command -v sudo >/dev/null 2>&1; then
    sudo zfs "$@"
    return $?
  fi

  return 1
}

overlay_mount_with_fallback() {
  local client="$1"
  local abs_lower="$2"
  local abs_upper="$3"
  local abs_work="$4"
  local merged="$5"

  local base_opts="lowerdir=$abs_lower,upperdir=$abs_upper,workdir=$abs_work"
  local volatile_suffix=""
  if [ "${OVERLAY_USE_VOLATILE,,}" = "true" ]; then
    volatile_suffix=",volatile"
  fi

  local candidate_opts=()
  if [ -n "$OVERLAY_MOUNT_EXTRA_OPTS" ]; then
    candidate_opts+=("$base_opts,$OVERLAY_MOUNT_EXTRA_OPTS")
  fi
  candidate_opts+=("$base_opts,metacopy=on,xino=auto,index=off,redirect_dir=off$volatile_suffix")
  candidate_opts+=("$base_opts,metacopy=on,xino=auto,redirect_dir=off$volatile_suffix")
  candidate_opts+=("$base_opts,xino=auto,redirect_dir=off$volatile_suffix")
  candidate_opts+=("$base_opts,redirect_dir=on$volatile_suffix")
  candidate_opts+=("$base_opts")

  local mount_opts
  for mount_opts in "${candidate_opts[@]}"; do
    if mount -t overlay overlay -o "$mount_opts" "$merged" 2>/dev/null; then
      echo "[INFO] Overlay mount options for $client: $mount_opts" >&2
      return 0
    fi

    if command -v sudo >/dev/null 2>&1 && sudo mount -t overlay overlay -o "$mount_opts" "$merged" >/dev/null 2>&1; then
      echo "[INFO] Overlay mount options for $client: $mount_opts (via sudo)" >&2
      return 0
    fi
  done

  return 1
}

resolve_zfs_dataset_for_path() {
  local target_path="$1"
  local target_abs
  target_abs=$(abspath "$target_path")

  local best_dataset=""
  local best_mount=""
  local best_len=0
  local dataset mountpoint

  while IFS=$'\t' read -r dataset mountpoint; do
    if [ -z "$dataset" ] || [ -z "$mountpoint" ] || [ "$mountpoint" = "-" ]; then
      continue
    fi

    case "$target_abs" in
      "$mountpoint"|"$mountpoint"/*)
        if [ "${#mountpoint}" -gt "$best_len" ]; then
          best_dataset="$dataset"
          best_mount="$mountpoint"
          best_len=${#mountpoint}
        fi
        ;;
    esac
  done < <(run_zfs list -H -o name,mountpoint -t filesystem 2>/dev/null)

  if [ -z "$best_dataset" ] || [ -z "$best_mount" ]; then
    return 1
  fi

  echo "$best_dataset|$best_mount"
}

prepare_overlay_for_client() {
  local client="$1"
  local network="$2"
  local snapshot_root="$3"

  if ! dir_has_content "$snapshot_root"; then
    echo "[ERROR] Snapshot directory for $client is missing or empty: $snapshot_root" >&2
    return 1
  fi

  local lower="$snapshot_root"
  local abs_lower
  abs_lower=$(abspath "$lower")
  echo "[INFO] Overlay snapshot source for $client (network=${network:-none}): $abs_lower" >&2
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
        echo "[ERROR] Failed to unmount previous overlay for $client" >&2
        return 1
      fi
    fi
  fi

  rm -rf "$merged" "$upper" "$work"
  mkdir -p "$merged" "$upper" "$work"

  local abs_upper abs_work
  abs_upper=$(abspath "$upper")
  abs_work=$(abspath "$work")
  if ! overlay_mount_with_fallback "$client" "$abs_lower" "$abs_upper" "$abs_work" "$merged"; then
    echo "[ERROR] Failed to mount overlay for $client (all mount option profiles failed)" >&2
    return 1
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
      echo "[WARN] Unable to unmount overlay for $client ($merged); leaving mount in place" >&2
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

ensure_zfs_runtime_parent_datasets() {
  local pool_name="$1"
  local client="$2"
  local base_dataset="$pool_name/$ZFS_RUNTIME_DATASET_ROOT"
  local client_dataset="$base_dataset/$client"

  if ! run_zfs list -H -o name "$base_dataset" >/dev/null 2>&1; then
    run_zfs create -o mountpoint=none "$base_dataset" >/dev/null 2>&1 || true
  fi
  if ! run_zfs list -H -o name "$client_dataset" >/dev/null 2>&1; then
    run_zfs create -o mountpoint=none "$client_dataset" >/dev/null 2>&1 || true
  fi
}

prepare_zfs_clone_for_client() {
  local client="$1"
  local network="$2"
  local snapshot_root="$3"

  local lower=""
  lower=$(resolve_snapshot_lower_for_client "$client" "$network" "$snapshot_root") || return 1

  if ! command -v zfs >/dev/null 2>&1; then
    echo "[ERROR] zfs command is not available but zfs backend was requested" >&2
    return 1
  fi

  local abs_lower
  abs_lower=$(abspath "$lower")

  local dataset_info=""
  dataset_info=$(resolve_zfs_dataset_for_path "$abs_lower") || {
    echo "[ERROR] Could not resolve ZFS dataset for snapshot path: $abs_lower" >&2
    return 1
  }

  local source_dataset="${dataset_info%%|*}"
  local source_mount="${dataset_info#*|}"
  local lower_suffix=""
  if [ "$abs_lower" != "$source_mount" ]; then
    lower_suffix="${abs_lower#"$source_mount"/}"
  fi

  local runtime_base
  runtime_base=$(overlay_base_from_lower "$abs_lower")
  local clone_id
  clone_id="$(date +%s%N)_$RANDOM"

  local zfs_mount_root="$runtime_base/$client/$clone_id"
  mkdir -p "$zfs_mount_root"

  local pool_name="${source_dataset%%/*}"
  ensure_zfs_runtime_parent_datasets "$pool_name" "$client"

  local clone_dataset="$pool_name/$ZFS_RUNTIME_DATASET_ROOT/$client/$clone_id"
  local snapshot_name="${source_dataset}@${ZFS_SNAPSHOT_PREFIX}_${client}_${clone_id}"

  if ! run_zfs snapshot "$snapshot_name"; then
    echo "[ERROR] Failed to create ZFS snapshot $snapshot_name" >&2
    return 1
  fi

  if ! run_zfs clone -o "mountpoint=$zfs_mount_root" "$snapshot_name" "$clone_dataset"; then
    echo "[ERROR] Failed to create ZFS clone $clone_dataset from $snapshot_name" >&2
    run_zfs destroy "$snapshot_name" >/dev/null 2>&1 || true
    return 1
  fi

  local data_dir="$zfs_mount_root"
  if [ -n "$lower_suffix" ]; then
    data_dir="$zfs_mount_root/$lower_suffix"
  fi

  if [ ! -d "$data_dir" ]; then
    echo "[ERROR] ZFS clone data directory not found: $data_dir" >&2
    run_zfs destroy -r -f "$clone_dataset" >/dev/null 2>&1 || true
    run_zfs destroy "$snapshot_name" >/dev/null 2>&1 || true
    return 1
  fi

  ACTIVE_ZFS_DATASETS["$client"]="$clone_dataset"
  ACTIVE_ZFS_SNAPSHOTS["$client"]="$snapshot_name"
  ACTIVE_ZFS_MOUNTS["$client"]="$zfs_mount_root"
  ACTIVE_ZFS_CLIENTS["$client"]=1

  echo "$data_dir"
}

cleanup_zfs_clone_for_client() {
  local client="$1"
  local clone_dataset="${ACTIVE_ZFS_DATASETS[$client]}"
  local snapshot_name="${ACTIVE_ZFS_SNAPSHOTS[$client]}"
  local mount_root="${ACTIVE_ZFS_MOUNTS[$client]}"

  if [ -n "$clone_dataset" ]; then
    run_zfs destroy -r -f "$clone_dataset" >/dev/null 2>&1 || true
  fi

  if [ -n "$snapshot_name" ]; then
    run_zfs destroy "$snapshot_name" >/dev/null 2>&1 || true
  fi

  if [ -n "$mount_root" ] && [ -d "$mount_root" ]; then
    rm -rf "$mount_root" >/dev/null 2>&1 || true
  fi

  unset ACTIVE_ZFS_DATASETS["$client"]
  unset ACTIVE_ZFS_SNAPSHOTS["$client"]
  unset ACTIVE_ZFS_MOUNTS["$client"]
  unset ACTIVE_ZFS_CLIENTS["$client"]
}

cleanup_all_zfs_clones() {
  local client
  for client in "${!ACTIVE_ZFS_CLIENTS[@]}"; do
    cleanup_zfs_clone_for_client "$client"
  done
}

cleanup_all_stale_zfs_clones() {
  if ! command -v zfs >/dev/null 2>&1; then
    return
  fi

  local stale_dataset
  while IFS= read -r stale_dataset; do
    if [ -z "$stale_dataset" ]; then
      continue
    fi
    run_zfs destroy -r -f "$stale_dataset" >/dev/null 2>&1 || true
  done < <(run_zfs list -H -o name -t filesystem 2>/dev/null | grep "/$ZFS_RUNTIME_DATASET_ROOT/" | sort -r)

  local stale_snapshot
  while IFS= read -r stale_snapshot; do
    if [ -z "$stale_snapshot" ]; then
      continue
    fi
    run_zfs destroy "$stale_snapshot" >/dev/null 2>&1 || true
  done < <(run_zfs list -H -o name -t snapshot 2>/dev/null | grep "@${ZFS_SNAPSHOT_PREFIX}_" || true)
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
      echo "[WARN] Unable to unmount stale overlay mount $mount_point" >&2
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

  if ! compose_detect >/dev/null 2>&1; then
    return
  fi

  if [ -f "$compose_dir/docker-compose.yaml" ]; then
    compose_cmd -f "$compose_dir/docker-compose.yaml" down --volumes >/dev/null 2>&1 || \
      compose_cmd -f "$compose_dir/docker-compose.yaml" down --volumes
  elif [ -d "$compose_dir" ]; then
    (
      cd "$compose_dir" && compose_cmd down --volumes >/dev/null 2>&1 || compose_cmd down --volumes
    )
  fi
}

docker_container_exists() {
  local name="$1"
  docker_cmd ps -a --format '{{.Names}}' | grep -Fxq "$name"
}

dump_client_logs() {
  local client_base="$1"
  if [ -z "$(resolve_docker_bin)" ]; then
    return
  fi
  mkdir -p logs
  local ts=$(date +%s)
  if docker_container_exists "gas-execution-client"; then
    docker_cmd logs gas-execution-client &> "logs/docker_${client_base}_${ts}.log" || true
  fi
  if docker_container_exists "gas-execution-client-sync"; then
    docker_cmd logs gas-execution-client-sync &> "logs/docker_sync_${client_base}_${ts}.log" || true
  fi
}

cleanup_on_exit() {
  local exit_status=$?
  trap - EXIT INT TERM

  if [ -n "$(resolve_docker_bin)" ]; then
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

  if [ "$USE_SNAPSHOT_BACKEND" = true ]; then
    if [ "$SNAPSHOT_BACKEND" = "zfs" ]; then
      if declare -F cleanup_all_zfs_clones >/dev/null 2>&1; then
        cleanup_all_zfs_clones
      fi
      if declare -F cleanup_all_stale_zfs_clones >/dev/null 2>&1; then
        cleanup_all_stale_zfs_clones
      fi
    else
      if declare -F cleanup_all_overlays >/dev/null 2>&1; then
        cleanup_all_overlays
      fi
      if declare -F cleanup_all_stale_overlay_mounts >/dev/null 2>&1; then
        cleanup_all_stale_overlay_mounts
      fi
    fi
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

while getopts "T:t:g:c:r:i:o:f:n:B:O:S:R:FW:" opt; do
  case $opt in
    T) TEST_PATHS_JSON="$OPTARG" ;;
    t) LEGACY_TEST_PATH="$OPTARG" ;;
    g) LEGACY_GENESIS_PATH="$OPTARG" ;;
    c) CLIENTS="$OPTARG" ;;
    r) RUNS="$OPTARG" ;;
    i) IMAGES="$OPTARG" ;;
    o) OPCODES_WARMUP_COUNT="$OPTARG" ;;
    f) FILTER="$OPTARG" ;;  # comma-separated exclude patterns
    n) NETWORK="$OPTARG"; USE_SNAPSHOT_BACKEND=true ;;
    B) SNAPSHOT_ROOT="$OPTARG"; USE_SNAPSHOT_BACKEND=true ;;
    O) OVERLAY_TMP_ROOT="$OPTARG"; USE_SNAPSHOT_BACKEND=true ;;
    S) SNAPSHOT_BACKEND="${OPTARG,,}"; USE_SNAPSHOT_BACKEND=true ;;
    R) RESTART_BEFORE_TESTING=true;;
    F) SKIP_FORKCHOICE=true;;
    W) WARMUP_OPCODES_PATH="$OPTARG" ;;
    *) echo "Usage: $0 [-t test_path] [-c clients] [-r runs] [-i images] [-o opcodesWarmupCount] [-f filter] [-n network] [-B snapshot_root] [-O runtime_root] [-S snapshot_backend(overlay|zfs)] [-F skipForkchoice] [-W warmup_opcodes_path]" >&2
       exit 1 ;;
  esac
done

if [ "$USE_SNAPSHOT_BACKEND" = true ]; then
  case "$SNAPSHOT_BACKEND" in
    overlay|zfs) ;;
    *)
      echo "[ERROR] Invalid snapshot backend '$SNAPSHOT_BACKEND'. Expected one of: overlay, zfs." >&2
      exit 1
      ;;
  esac

  if [ "$SNAPSHOT_BACKEND" = "zfs" ] && ! command -v zfs >/dev/null 2>&1; then
    echo "[ERROR] zfs backend requested but 'zfs' command is not available." >&2
    exit 1
  fi
fi


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
    echo "[ERROR] You must provide either -T <json> or -t <test_path>"
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

trap cleanup_on_exit EXIT INT TERM

mkdir -p results warmupresults logs

# Initialize debug file if specified
if [ "$SKIP_FORKCHOICE" = true ]; then
  SKIP_FORKCHOICE_OPT=" --skipForkchoice"
else
  SKIP_FORKCHOICE_OPT=""
fi

if [ "$USE_SNAPSHOT_BACKEND" = true ]; then
  if [ "$SNAPSHOT_BACKEND" = "zfs" ]; then
    cleanup_all_stale_zfs_clones
  else
    cleanup_all_stale_overlay_mounts
  fi

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
python3 -m pip install --user --ignore-installed -r requirements.txt
prepare_tools_success=false
for attempt in 1 2 3; do
  echo "[INFO] Running make prepare_tools (attempt $attempt/3)"
  if make prepare_tools; then
    prepare_tools_success=true
    break
  fi
  if [ "$attempt" -lt 3 ]; then
    sleep_seconds=$((attempt * 30))
    echo "[WARN] make prepare_tools failed, retrying in ${sleep_seconds}s..."
    sleep "$sleep_seconds"
  fi
done

if [ "$prepare_tools_success" != true ]; then
  echo "[ERROR] make prepare_tools failed after 3 attempts"
  exit 1
fi

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

    if [ "$NETWORK" = "perf-devnet-2" ] || [ "$NETWORK" = "perf-devnet-3" ]; then
      devnet_genesis="perf-devnet-2-osaka.json"
      if [ "$NETWORK" = "perf-devnet-3" ]; then
        devnet_genesis="perf-devnet-3-osaka.json"
      fi
      case "$client_base" in
        besu)
          raw_genesis="$devnet_genesis"
          genesis_client="besu"
          ;;
        geth|reth|erigon|nimbus|ethrex)
          raw_genesis="$devnet_genesis"
          genesis_client="geth"
          ;;
        nethermind)
          raw_genesis=""
          ;;
      esac
    fi

    if [ -n "$raw_genesis" ]; then
      if [ "$genesis_client" != "besu" ] && [ "$genesis_client" != "nethermind" ]; then
        genesis_client="geth"
      fi
      genesis_path="scripts/genesisfiles/$genesis_client/$raw_genesis"
    else
      genesis_path=""
    fi

    data_dir=""
    if [ "$USE_SNAPSHOT_BACKEND" = true ]; then
      snapshot_root_for_client=$(resolve_snapshot_root_for_client "$client_base" "$NETWORK")
      if [ -z "$snapshot_root_for_client" ]; then
        echo "[ERROR] Snapshot root not specified for $client" >&2
        if [ "$SNAPSHOT_BACKEND" = "zfs" ]; then
          cleanup_zfs_clone_for_client "$client_base"
        else
          cleanup_overlay_for_client "$client_base"
        fi
        continue
      fi
      if [ "$SNAPSHOT_BACKEND" = "zfs" ]; then
        data_dir=$(prepare_zfs_clone_for_client "$client_base" "$NETWORK" "$snapshot_root_for_client") || {
          echo "[ERROR] Skipping $client - ZFS clone setup failed" >&2
          cleanup_zfs_clone_for_client "$client_base"
          continue
        }
      else
        data_dir=$(prepare_overlay_for_client "$client_base" "$NETWORK" "$snapshot_root_for_client") || {
          echo "[ERROR] Skipping $client - overlay setup failed" >&2
          cleanup_overlay_for_client "$client_base"
          continue
        }
      fi
    else
      data_dir=$(abspath "scripts/$client_base/execution-data")
      mkdir -p "$data_dir"
    fi

    volume_name="${client_base}_$(date +%s)_$RANDOM"
    if [ "$USE_SNAPSHOT_BACKEND" = true ]; then
      runtime_root=""
      if [ "$SNAPSHOT_BACKEND" = "zfs" ]; then
        runtime_root="${ACTIVE_ZFS_MOUNTS[$client_base]}"
      else
        runtime_root="${ACTIVE_OVERLAY_ROOTS[$client_base]}"
      fi
      if [ -n "$runtime_root" ]; then
        runtime_token=$(basename "$runtime_root")
        volume_name="${client_base}_${runtime_token}_$(date +%s)_$RANDOM"
      fi
    fi
    volume_name=$(echo "$volume_name" | tr -cd '[:alnum:]._-')
    if [ -z "$volume_name" ]; then
      volume_name="${client_base}_volume"
    fi


    setup_cmd=(python3 setup_node.py --client "$client" --imageBulk "$IMAGES" --dataDir "$data_dir")
    if [ "$USE_SNAPSHOT_BACKEND" = true ]; then
      setup_cmd+=(--dataBackend "$SNAPSHOT_BACKEND")
    else
      setup_cmd+=(--dataBackend "direct")
    fi
    if [ -n "$NETWORK" ]; then
      if { [ "$NETWORK" = "perf-devnet-2" ] || [ "$NETWORK" = "perf-devnet-3" ]; } && [ -n "$genesis_path" ] && [ "$client_base" != "nethermind" ]; then
        echo "Using custom genesis for $client: $genesis_path"
        setup_cmd+=(--genesisPath "$genesis_path")
      else
        setup_cmd+=(--network "$NETWORK")
      fi
    elif [ -n "$genesis_path" ]; then
      echo "Using custom genesis for $client: $genesis_path"
      setup_cmd+=(--genesisPath "$genesis_path")
    fi
    setup_cmd+=(--volumeName "$volume_name")

    RUNNING_CLIENTS["$client_base"]=1

    echo "[INFO] Running setup_node command: ${setup_cmd[*]}"
    "${setup_cmd[@]}"

    # Nethermind on perf-devnet-3 requires FlatDb state format
    if [ "$client_base" = "nethermind" ] && [ "$NETWORK" = "perf-devnet-3" ]; then
      echo "NETHERMIND_EXTRA_OPTS=--FlatDb.Enabled=true" >> "scripts/nethermind/.env"
    fi

    if declare -f wait_for_rpc >/dev/null 2>&1; then
      wait_for_rpc "http://127.0.0.1:8545" "$RPC_READINESS_MAX_ATTEMPTS"
    else
      sleep 5
    fi

    # Reth mainnet snapshot is 2 blocks behind; bootstrap via FCU + p2p
    if [ "$client_base" = "reth" ] && [ "$NETWORK" = "mainnet" ]; then
      bootstrap_fcu "0x6aadde478df4f485c2cf91cd48038f918ef6ff97b19eec9cdd0cd1ca45476eb4" 60 30
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
          echo "[WARN] Skipping $filename for $client - restart failed" >&2
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
              if (( OPCODES_WARMUP_COUNT > 1 && warmup_count > 1 )); then
                # Iterations 2+: create variant with unique prevRandao so the
                # client treats it as a new block and re-executes the opcodes.
                variant_file=$(mktemp --suffix=.txt)
                probe_dir=$(mktemp -d)
                python3 vary_warmup.py create "$warmup_path" "$warmup_count" "$variant_file"

                echo "[INFO] Warmup $warmup_count/$OPCODES_WARMUP_COUNT: probe send to discover blockHash"
                python3 run_kute.py --output "$probe_dir" --testsPath "$variant_file" --jwtPath /tmp/jwtsecret --client $client --run $run --kuteArguments '-f engine_newPayload'$SKIP_FORKCHOICE_OPT

                if python3 vary_warmup.py fix-hashes "$variant_file" "$probe_dir" "$client"; then
                  echo "[INFO] Warmup $warmup_count/$OPCODES_WARMUP_COUNT: re-send with corrected blockHash"
                  python3 run_kute.py --output warmupresults --testsPath "$variant_file" --jwtPath /tmp/jwtsecret --client $client --run $run --kuteArguments '-f engine_newPayload'$SKIP_FORKCHOICE_OPT
                else
                  echo "[WARN] Warmup $warmup_count/$OPCODES_WARMUP_COUNT: hash fix failed, skipping re-send"
                fi

                rm -f "$variant_file"
                rm -rf "$probe_dir"
              else
                echo "[INFO] Running opcode warmup run_kute command: python3 run_kute.py --output warmupresults --testsPath \"$warmup_path\" --jwtPath /tmp/jwtsecret --client $client --run $run --kuteArguments '-f engine_newPayload'$SKIP_FORKCHOICE_OPT"
                python3 run_kute.py --output warmupresults --testsPath "$warmup_path" --jwtPath /tmp/jwtsecret --client $client --run $run --kuteArguments '-f engine_newPayload'$SKIP_FORKCHOICE_OPT
              fi
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

    if [ "$USE_SNAPSHOT_BACKEND" = true ]; then
      if [ "$SNAPSHOT_BACKEND" = "zfs" ]; then
        cleanup_zfs_clone_for_client "$client_base"
        cleanup_all_stale_zfs_clones
      else
        cleanup_overlay_for_client "$client_base"
        cleanup_all_stale_overlay_mounts
      fi
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
if ls logs/docker_* 1>/dev/null 2>&1; then
  mkdir -p reports/docker
  cp -r logs/docker_* reports/docker
fi
if command -v zip &>/dev/null; then
  zip -r reports.zip reports
else
  tar -czf reports.tar.gz reports
fi

# Print timing summary at the end
