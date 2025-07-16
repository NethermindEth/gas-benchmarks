#!/bin/bash

# This script runs metrics posting commands in an infinite loop.
# It accepts the following command line arguments:
#   --table-name             The table name to use.
#   --db-user                The database user.
#   --db-host                The database host.
#   --db-password            The database password.
#   --warmup                 The warmup file (default: warmup/warmup-1000bl-16wi-24tx.txt)
#   --prometheus-endpoint    The Prometheus endpoint URL.
#   --prometheus-username    The Prometheus basic auth username.
#   --prometheus-password    The Prometheus basic auth password.
#   --test-paths-json        JSON string of test path entries (each with path + optional genesis)

# Default warmup file
WARMUP_FILE="warmup/warmup-1000bl-16wi-24tx.txt"
TEST_PATHS_JSON='[{"path": "tests-vm"}]'  # default test path

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

# Infinite benchmark loop
while true; do
  # Start timing for this loop iteration
  LOOP_START_TIME=$(date +%s.%N)
  debug_log "Starting new loop iteration"
  
  start_timer "git_pull"
  git pull

  # Update performance images
  python3 update_performance_images.py

  # Run benchmark tests
  PROMETHEUS_ENDPOINT="$PROMETHEUS_ENDPOINT" \
  PROMETHEUS_USERNAME="$PROMETHEUS_USERNAME" \
  PROMETHEUS_PASSWORD="$PROMETHEUS_PASSWORD" \
  bash run.sh -T "$TEST_PATHS_JSON" -w "$WARMUP_FILE" -r1

  # Push metrics to Postgres
  python3 fill_postgres_db.py \
    --db-host "$DB_HOST" \
    --db-port 5432 \
    --db-user "$DB_USER" \
    --db-name monitoring \
    --table-name "$TABLE_NAME" \
    --db-password "$DB_PASSWORD" \
    --reports-dir 'reports'

  # Cleanup
  rm -rf reports/
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
