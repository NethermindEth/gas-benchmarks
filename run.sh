#!/bin/bash

# Default inputs
TEST_PATH="tests/"
WARMUP_FILE="warmup/warmup-1000bl-16wi-24tx.txt"
CLIENTS="nethermind,nethermind-modexp,geth,reth,besu,erigon,nimbus"
RUNS=8
IMAGES='{"nethermind":"default","nethermind-modexp":"default","geth":"default","reth":"default","erigon":"default","besu":"default","nimbus":"default"}'
EXECUTIONS_FILE="executions.json"

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
  
  if [ ! -f "$EXECUTIONS_FILE" ]; then
    return 1  # File doesn't exist, so not executed
  fi
  
  # Extract last execution date for the client using jq
  local last_execution=$(jq -r --arg client "$client" '.[$client] // empty' "$EXECUTIONS_FILE" 2>/dev/null | cut -d'T' -f1)
  
  if [ "$last_execution" = "$today" ]; then
    return 0  # Was executed today
  else
    return 1  # Was not executed today
  fi
}

# Function to update executions.json with current timestamp
update_execution_time() {
  local client=$1
  local timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  
  # Update JSON file using jq
  local temp_file=$(mktemp)
  jq --arg client "$client" --arg timestamp "$timestamp" '.[$client] = $timestamp' "$EXECUTIONS_FILE" > "$temp_file" && mv "$temp_file" "$EXECUTIONS_FILE"
  
  echo "Updated execution time for $client: $timestamp"
}

# Parse command line arguments
while getopts "t:w:c:r:i:x" opt; do
  case $opt in
    t) TEST_PATH="$OPTARG" ;;
    w) WARMUP_FILE="$OPTARG" ;;
    c) CLIENTS="$OPTARG" ;;
    r) RUNS="$OPTARG" ;;
    i) IMAGES="$OPTARG" ;;
    *) echo "Usage: $0 [-t test_path] [-w warmup_file] [-c clients] [-r runs] [-i images] [-x]" >&2
       exit 1 ;;
  esac
done

IFS=',' read -ra CLIENT_ARRAY <<< "$CLIENTS"

# Set up environment
rm -rf results
mkdir -p results
mkdir -p logs

# Initialize executions tracking
init_executions_file

# Install dependencies
pip install -r requirements.txt
make prepare_tools

# Find leaf directories
LEAF_DIRS=$(find "$TEST_PATH" -type d | while read -r dir; do
  if [ -z "$(find "$dir" -mindepth 1 -maxdepth 1 -type d)" ]; then
    echo "$dir"
  fi
done)

ts=$(date +%s)

# Run benchmarks
for run in $(seq 1 $RUNS); do
  for client in "${CLIENT_ARRAY[@]}"; do
    # Check if nimbus was already executed today
    if [ "$client" = "nimbus" ] && was_executed_today "$client"; then
      echo "Skipping $client - already executed today"
      continue
    fi
    
    for test_dir in $LEAF_DIRS; do
      if [ -z "$IMAGES" ]; then
        python3 setup_node.py --client $client
      else
        echo "Using provided image: $IMAGES for $client"
        python3 setup_node.py --client $client --imageBulk "$IMAGES"
      fi

      if [ -z "$WARMUP_FILE" ]; then
        echo "Running script without warm up."
        python3 run_kute.py --output results --testsPath "$test_dir" --jwtPath /tmp/jwtsecret --client $client --run $run
      else
        echo "Using provided warm up file: $WARMUP_FILE"
        python3 run_kute.py --output results --testsPath "$test_dir" --jwtPath /tmp/jwtsecret --warmupPath "$WARMUP_FILE" --client $client --run $run
      fi

      docker logs gas-execution-client 2> logs/docker_$client_$ts.log

      cl_name=$(echo "$client" | cut -d '_' -f 1)
      cd "scripts/$cl_name"
      docker compose down
      sudo rm -rf execution-data
      cd ../..
    done
    
    # Update execution time after successful completion
    update_execution_time "$client"
  done
done

# Process results
if [ -z "$IMAGES" ]; then
  python3 report_tables.py --resultsPath results --clients "$CLIENTS" --testsPath "$TEST_PATH" --runs $RUNS
  python3 report_html.py --resultsPath results --clients "$CLIENTS" --testsPath "$TEST_PATH" --runs $RUNS
else
  python3 report_tables.py --resultsPath results --clients "$CLIENTS" --testsPath "$TEST_PATH" --runs $RUNS --images "$IMAGES"
  python3 report_html.py --resultsPath results --clients "$CLIENTS" --testsPath "$TEST_PATH" --runs $RUNS --images "$IMAGES"
fi

# Prepare and zip the results
zip -r reports.zip reports
