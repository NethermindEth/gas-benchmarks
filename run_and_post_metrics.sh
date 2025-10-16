#!/bin/bash

# This script runs metrics posting commands in an infinite loop.
# It accepts the following command line arguments:
#   --table-name   The table name to use.
#   --db-user      The database user.
#   --db-host      The database host.
#   --db-password  The database password.
#   --warmup       The warmup file (default: warmup/warmup-1000bl-16wi-24tx.txt)
#   --prometheus-endpoint   The Prometheus endpoint URL.
#   --prometheus-username   The Prometheus basic auth username.
#   --prometheus-password   The Prometheus basic auth password.
#   --debug        Enable debug mode with detailed timing
#   --debug-file   Enable debug mode and save output to specified file
#   --profile-test Enable test-specific profiling (shows individual test timings)
#
# Example usage:
#   nohup ./run_and_post_metrics.sh --table-name gas_limit_benchmarks --db-user nethermind --db-host perfnet.core.nethermind.dev --db-password "MyPass" --warmup "warmup/mycustom.txt" --debug &
#   nohup ./run_and_post_metrics.sh --table-name gas_limit_benchmarks --db-user nethermind --db-host perfnet.core.nethermind.dev --db-password "MyPass" --debug-file "debug.log" --profile-test &
#
# Default warmup file is set to "warmup/warmup-1000bl-16wi-24tx.txt"

# Default warmup file
WARMUP_FILE="warmup/warmup-1000bl-16wi-24tx.txt"
TEST_PATHS_JSON='[{\"path\": \"eest_tests\", \"genesis\":\"zkevmgenesis.json\"}]'  # default test path
DEBUG_FLAG=""
DEBUG=false
DEBUG_FILE=""

# Timing variables
declare -A STEP_TIMES
SCRIPT_START_TIME=$(date +%s.%N)

# Cleanup function
cleanup() {
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
  
  debug_log "Script cleanup completed"
}

# Set up signal handlers for cleanup
trap cleanup EXIT INT TERM

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

print_timing_summary() {
  if [ "$DEBUG" = true ]; then
    local summary=""
    summary+="\n"
    summary+="=== LOOP TIMING SUMMARY ===\n"
    local total_time=$(awk "BEGIN {printf \"%.2f\", $(date +%s.%N) - $LOOP_START_TIME}")
    summary+="Total loop time: ${total_time}s\n"
    summary+="\n"
    
    for key in "${!STEP_TIMES[@]}"; do
      if [[ "$key" == *"_duration" ]]; then
        local step_name="${key%_duration}"
        local duration="${STEP_TIMES[$key]}"
        summary+="$(printf "%-30s: %8ss\n" "$step_name" "$duration")"
      fi
    done
    summary+="===========================\n"
    
    # Print to stdout
    echo -e "$summary"
    
    # Save to file if specified
    if [ -n "$DEBUG_FILE" ]; then
      echo -e "$summary" >> "$DEBUG_FILE"
    fi
  fi
}



# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
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
    --warmup)
      WARMUP_FILE="$2"
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
      if [ -z "$DEBUG_FLAG" ]; then
        DEBUG_FLAG="-d"
      else
        DEBUG_FLAG="$DEBUG_FLAG -d"
      fi
      shift
      ;;
    --debug-file)
      DEBUG=true
      DEBUG_FILE="$2"
      if [ -z "$DEBUG_FLAG" ]; then
        DEBUG_FLAG="-D \"$2\"_detailed"
      else
        DEBUG_FLAG="$DEBUG_FLAG -D \"$2\""
      fi
      shift 2
      ;;
    --profile-test)
      if [ -z "$DEBUG_FLAG" ]; then
        DEBUG_FLAG="-d -p"
      else
        DEBUG_FLAG="$DEBUG_FLAG -p"
      fi
      shift
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

if [[ -z "$TABLE_NAME" || -z "$DB_USER" || -z "$DB_HOST" || -z "$DB_PASSWORD" ]]; then
  echo "Usage: $0 --table-name <table_name> --db-user <db_user> --db-host <db_host> --db-password <db_password> [--warmup <warmup_file> --prometheus-endpoint <prometheus_endpoint> --prometheus-username <prometheus_username> --prometheus-password <prometheus_password> --test-paths-json <json>]"
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

# Run commands in an infinite loop
while true; do
  # Start timing for this loop iteration
  LOOP_START_TIME=$(date +%s.%N)
  debug_log "Starting new loop iteration"
  start_timer "git_pull"
  git pull
  git lfs pull
  end_timer "git_pull"
  
  start_timer "update_performance_images"
  # Update performance images
  python3 update_performance_images.py
  end_timer "update_performance_images"
  
  start_timer "benchmark_testing"
  # Run the benchmark testing using specified warmup file
  PROMETHEUS_ENDPOINT="$PROMETHEUS_ENDPOINT" PROMETHEUS_USERNAME="$PROMETHEUS_USERNAME" PROMETHEUS_PASSWORD="$PROMETHEUS_PASSWORD" \
    eval "bash run.sh -T \"$TEST_PATHS_JSON\" -w \"$WARMUP_FILE\" -r1 -r1 $DEBUG_FLAG"
  end_timer "benchmark_testing"

  start_timer "populate_postgres_db_background"
  # Create unique backup directory with timestamp
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  BACKUP_DIR="reports_backup_$TIMESTAMP"
  
  # Clean up old backup directories (keep only 2 newest)
  if ls reports_backup_* 1> /dev/null 2>&1; then
    debug_log "Cleaning up old backup directories..."
    # Get all backup directories sorted by modification time (newest first)
    # Keep only the 2 newest, remove the rest
    ls -dt reports_backup_* | tail -n +3 | xargs -r rm -rf
    debug_log "Cleanup completed"
  fi
  
  # Create new backup and start background process
  cp -r reports/ "$BACKUP_DIR"
  debug_log "Created backup directory: $BACKUP_DIR"
  
  # Populate the Postgres DB with the metrics data
  python3 fill_postgres_db.py --db-host "$DB_HOST" --db-port 5432 --db-user "$DB_USER" --db-name monitoring --table-name "$TABLE_NAME" --db-password "$DB_PASSWORD" --reports-dir "$BACKUP_DIR" &
  end_timer "populate_postgres_db_background"

  start_timer "cleanup_reports"
  # Clean up the reports directory
  rm -rf reports/
  end_timer "cleanup_reports"

  start_timer "revert_images"
  # Revert images.yml to original state
  python3 update_performance_images.py --revert
  end_timer "revert_images"
  
  # Print timing summary for this loop iteration
  print_timing_summary
  
  # Clear timing data for next iteration
  unset STEP_TIMES
  declare -A STEP_TIMES
  
  debug_log "Loop iteration completed"
  echo "--- End of loop iteration ---"
done
