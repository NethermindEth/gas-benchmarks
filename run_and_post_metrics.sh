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
#
# Example usage:
#   nohup ./run_and_post_metrics.sh --table-name gas_limit_benchmarks --db-user nethermind --db-host perfnet.core.nethermind.dev --db-password "MyPass" --warmup "warmup/mycustom.txt" &
#
# Default warmup file is set to "warmup/warmup-1000bl-16wi-24tx.txt"

# Default value for warmup file
WARMUP_FILE="warmup/warmup-1000bl-16wi-24tx.txt"

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
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

if [[ -z "$TABLE_NAME" || -z "$DB_USER" || -z "$DB_HOST" || -z "$DB_PASSWORD" ]]; then
  echo "Usage: $0 --table-name <table_name> --db-user <db_user> --db-host <db_host> --db-password <db_password> [--warmup <warmup_file> --prometheus-endpoint <prometheus_endpoint> --prometheus-username <prometheus_username> --prometheus-password <prometheus_password>]"
  exit 1
fi

# Run commands in an infinite loop
while true; do
  git pull
  # Update performance images
  python3 update_performance_images.py
  # Run the benchmark testing using specified warmup file
  PROMETHEUS_ENDPOINT="$PROMETHEUS_ENDPOINT" PROMETHEUS_USERNAME="$PROMETHEUS_USERNAME" PROMETHEUS_PASSWORD="$PROMETHEUS_PASSWORD" \
    bash run.sh -t "tests-vm/" -w "$WARMUP_FILE" -r1

  # Populate the Postgres DB with the metrics data
  python3 fill_postgres_db.py --db-host "$DB_HOST" --db-port 5432 --db-user "$DB_USER" --db-name monitoring --table-name "$TABLE_NAME" --db-password "$DB_PASSWORD" --reports-dir 'reports'

  # Clean up the reports directory
  rm -rf reports/

  # Revert images.yml to original state
  python3 update_performance_images.py --revert
done
