#!/bin/bash

# This script runs metrics posting commands in an infinite loop.
# It accepts the following command line arguments:
#   --table-name   The table name to use.
#   --db-user      The database user.
#   --db-host      The database host.
#   --db-password  The database password.
#   --prometheus-endpoint   The Prometheus endpoint URL.
#   --prometheus-username   The Prometheus basic auth username.
#   --prometheus-password   The Prometheus basic auth password.
#   --network      Network name forwarded to run.sh (e.g. mainnet)
#   --snapshot-root Base directory for overlay snapshots (can include placeholders)
#   --snapshot-template Optional template appended to snapshot root (supports <<CLIENT>> / <<NETWORK>>)
#   --clients      Comma-separated client list forwarded to run.sh
#   --restarts     true/false to control client restarts (-R flag for run.sh)
#   --debug        Enable debug logging for this script
#   --debug-file   Enable debug logging and save output to specified file
#   --max-loops    Optional integer to stop after N iterations (default: unlimited)
#   --warmup-opcodes-path Path to opcode warmup payloads directory (default: warmup-tests)
#
# Example usage:
#   nohup ./run_and_post_metrics.sh --table-name gas_limit_benchmarks --db-user nethermind --db-host perfnet.core.nethermind.dev --db-password "MyPass" --debug &
#   nohup ./run_and_post_metrics.sh --table-name gas_limit_benchmarks --db-user nethermind --db-host perfnet.core.nethermind.dev --db-password "MyPass" --debug-file "debug.log" &

TEST_PATHS_JSON='[{"path":"eest_tests","genesis":"zkevmgenesis.json"}]'  # default test path
DEBUG=false
DEBUG_FILE=""
NETWORK=""
NETWORK_LABEL="all"
SNAPSHOT_ROOT=""
SNAPSHOT_TEMPLATE=""
CLIENTS=""
CLIENTS_LABEL="all"
RESTART_BEFORE_TESTING=false
MAX_LOOPS=""
WARMUP_OPCODES_PATH=""
SKIP_CLEANUP=false
CLEANUP_ARMED=false
parse_bool() {
  case "$(echo "$1" | tr '[:upper:]' '[:lower:]')" in
    true|1|yes|on) echo true ;;
    false|0|no|off) echo false ;;
    *) echo "invalid" ;;
  esac
}

sanitize_label() {
  local value="${1:-}"
  if [ -z "$value" ]; then
    echo "none"
    return
  fi
  local lowered
  lowered=$(echo "$value" | tr '[:upper:]' '[:lower:]')
  lowered="${lowered// /_}"
  lowered="${lowered//[^a-z0-9._-]/_}"
  # Collapse multiple underscores
  lowered=$(echo "$lowered" | sed 's/_\+/_/g;s/^_//;s/_$//')
  if [ -z "$lowered" ]; then
    echo "none"
  else
    echo "$lowered"
  fi
}

# Cleanup function
cleanup() {
  if [ "$SKIP_CLEANUP" = true ] || [ "$CLEANUP_ARMED" = false ]; then
    return
  fi
  debug_log "Script cleanup initiated"
  
  # Stop and delete the gas-execution-client container
  if docker ps -a --format "table {{.Names}}" | grep -q "gas-execution-client"; then
    debug_log "Stopping gas-execution-client container..."
    docker stop gas-execution-client 2>/dev/null || true
    debug_log "Removing gas-execution-client container..."
    docker rm gas-execution-client 2>/dev/null || true
  else
    debug_log "gas-execution-client container not found"
  fi
  
  # Remove script/*/execution-data folders
  debug_log "Removing script/*/execution-data folders..."
  find scripts/ -type d -name "execution-data" -exec rm -r {} + 2>/dev/null || true

  # Stop and remove all containers, then prune Docker resources
  if command -v docker >/dev/null 2>&1; then
    debug_log "Stopping all running containers..."
    docker ps -q | xargs -r docker stop 2>/dev/null || true
    debug_log "Removing all containers..."
    docker ps -aq | xargs -r docker rm -f 2>/dev/null || true
    debug_log "Pruning all Docker resources (including volumes)..."
    docker system prune -af --volumes 2>/dev/null || true
  fi
  
  debug_log "Script cleanup completed"
}

# Set up signal handlers for cleanup
trap cleanup EXIT INT TERM

usage() {
  echo "Usage: $0 --table-name <table_name> --db-user <db_user> --db-host <db_host> --db-password <db_password> [--warmup-opcodes-path <dir> --prometheus-endpoint <prometheus_endpoint> --prometheus-username <prometheus_username> --prometheus-password <prometheus_password> --test-paths-json <json> --network <network> --snapshot-root <path> --snapshot-template <template> --clients <client_list> --restarts <true|false> --max-loops <N>]"
}

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

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      SKIP_CLEANUP=true
      usage
      exit 0
      ;;
    --table-name)
      TABLE_NAME="$2"
      shift 2
      ;;
    --db-user)
      DB_USER="$2"
      shift 2
      ;;
    --db-host)
      DB_HOST="$2"
      shift 2
      ;;
    --db-password)
      DB_PASSWORD="$2"
      shift 2
      ;;
    --prometheus-endpoint)
      PROMETHEUS_ENDPOINT="$2"
      shift 2
      ;;
    --prometheus-username)
      PROMETHEUS_USERNAME="$2"
      shift 2
      ;;
    --prometheus-password)
      PROMETHEUS_PASSWORD="$2"
      shift 2
      ;;
    --test-paths-json)
      TEST_PATHS_JSON="$2"
      shift 2
      ;;
    --debug)
      DEBUG=true
      shift
      ;;
    --debug-file)
      DEBUG=true
      DEBUG_FILE="$2"
      shift 2
      ;;
    --network)
      NETWORK="$2"
      NETWORK_LABEL=$(sanitize_label "$NETWORK")
      shift 2
      ;;
    --snapshot-root)
      SNAPSHOT_ROOT="$2"
      shift 2
      ;;
    --snapshot-template)
      SNAPSHOT_TEMPLATE="$2"
      shift 2
      ;;
    --clients)
      CLIENTS="$2"
      CLIENTS_LABEL=$(sanitize_label "$CLIENTS")
      shift 2
      ;;
    --restart-before-testing)
      RESTART_BEFORE_TESTING=true
      shift
      ;;
    --restarts)
      value=$(parse_bool "$2")
      if [ "$value" = "invalid" ]; then
        echo "Invalid value for --restarts: $2 (expected true/false)"
        exit 1
      fi
      RESTART_BEFORE_TESTING=$value
      shift 2
      ;;
    --warmup-opcodes-path)
      WARMUP_OPCODES_PATH="$2"
      shift 2
      ;;
    --max-loops)
      if [[ "$2" =~ ^[0-9]+$ && "$2" -gt 0 ]]; then
        MAX_LOOPS="$2"
      else
        echo "Invalid value for --max-loops: $2 (expected positive integer)"
        exit 1
      fi
      shift 2
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done



if [[ -z "$TABLE_NAME" || -z "$DB_USER" || -z "$DB_HOST" || -z "$DB_PASSWORD" ]]; then
  usage
  exit 1
fi

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
    echo "Debug file '$DEBUG_FILE' already exists, using '$DEBUG_FILE' instead"
  fi
fi

# Run commands in an infinite loop (optionally bounded by --max-loops)
CLEANUP_ARMED=true
loops_done=0
while true; do
  loops_done=$((loops_done + 1))
  debug_log "Starting new loop iteration"
  git pull
  git lfs pull
  
  # Update performance images
  python3 update_performance_images.py
  
  # Run the benchmark testing using opcode warmups only
  RUN_CMD=(bash run.sh -T "$TEST_PATHS_JSON" -r 1)
  if [ -n "$NETWORK" ]; then
    RUN_CMD+=(-n "$NETWORK")
  fi

  snapshot_arg="$SNAPSHOT_ROOT"
  if [ -n "$SNAPSHOT_TEMPLATE" ]; then
    if [ -n "$snapshot_arg" ]; then
      snapshot_arg="${snapshot_arg%/}/$SNAPSHOT_TEMPLATE"
    else
      snapshot_arg="$SNAPSHOT_TEMPLATE"
    fi
  fi
  if [ -n "$snapshot_arg" ]; then
    RUN_CMD+=(-B "$snapshot_arg")
  fi

  if [ -n "$CLIENTS" ]; then
    RUN_CMD+=(-c "$CLIENTS")
  fi

  if [ "$RESTART_BEFORE_TESTING" = true ]; then
    RUN_CMD+=(-R true)
  fi
  if [ -n "$WARMUP_OPCODES_PATH" ]; then
    RUN_CMD+=(-W "$WARMUP_OPCODES_PATH")
  fi

  echo "[INFO] Executing benchmark command: ${RUN_CMD[*]}"
  PROMETHEUS_ENDPOINT="$PROMETHEUS_ENDPOINT" \
  PROMETHEUS_USERNAME="$PROMETHEUS_USERNAME" \
  PROMETHEUS_PASSWORD="$PROMETHEUS_PASSWORD" \
    "${RUN_CMD[@]}"

  # Create unique backup directory with timestamp
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  BACKUP_PREFIX="reports_backup_${CLIENTS_LABEL}_${NETWORK_LABEL}"
  BACKUP_DIR="${BACKUP_PREFIX}_${TIMESTAMP}"

  # Clean up old backup directories (keep only 2 newest per client/network)
  shopt -s nullglob
  backup_candidates=("${BACKUP_PREFIX}"_*)
  shopt -u nullglob
  if [ ${#backup_candidates[@]} -gt 0 ]; then
    debug_log "Cleaning up old backup directories..."
    ls -dt "${backup_candidates[@]}" | tail -n +3 | xargs -r rm -rf
    debug_log "Cleanup completed"
  fi
  
  # Create new backup and start background process
  cp -r reports/ "$BACKUP_DIR"
  debug_log "Created backup directory: $BACKUP_DIR"
  
  # Populate the Postgres DB with the metrics data
  python3 fill_postgres_db.py --db-host "$DB_HOST" --db-port 5432 --db-user "$DB_USER" --db-name monitoring --table-name "$TABLE_NAME" --db-password "$DB_PASSWORD" --reports-dir "$BACKUP_DIR" &

  # Clean up the reports directory
  rm -rf reports/

  # Revert images.yml to original state
  python3 update_performance_images.py --revert
  
  debug_log "Loop iteration completed"
  echo "--- End of loop iteration ---"

  if [ -n "$MAX_LOOPS" ] && [ "$loops_done" -ge "$MAX_LOOPS" ]; then
    echo "[INFO] Reached max loops ($MAX_LOOPS); exiting."
    exit 0
  fi
done

