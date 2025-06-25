#!/bin/bash

# Default inputs
TEST_PATH="tests/"
WARMUP_OPCODES_PATH="warmup-tests"
WARMUP_FILE="warmup/warmup-1000bl-16wi-24tx.txt"
CLIENTS="nethermind,geth,reth,besu,erigon,nimbus"
RUNS=8
OPCODES_WARMUP_COUNT=2
FILTER="Modexp"
IMAGES='{"nethermind":"default","geth":"default","reth":"default","erigon":"default","besu":"default","nimbus":"default"}'
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
    return 1
  fi
  
  local last_execution=$(jq -r --arg client "$client" '.[$client] // empty' "$EXECUTIONS_FILE" 2>/dev/null | cut -d'T' -f1)
  
  if [ "$last_execution" = "$today" ]; then
    return 0
  else
    return 1
  fi
}

# Function to update executions.json with current timestamp
update_execution_time() {
  local client=$1
  local timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  
  local temp_file=$(mktemp)
  jq --arg client "$client" --arg timestamp "$timestamp" '.[$client] = $timestamp' "$EXECUTIONS_FILE" > "$temp_file" && mv "$temp_file" "$EXECUTIONS_FILE"
  
  echo "Updated execution time for $client: $timestamp"
}

# Parse command line arguments
while getopts "t:w:c:r:i:o:f:" opt; do
  case $opt in
    t) TEST_PATH="$OPTARG" ;;
    w) WARMUP_FILE="$OPTARG" ;;
    c) CLIENTS="$OPTARG" ;;
    r) RUNS="$OPTARG" ;;
    i) IMAGES="$OPTARG" ;;
    o) OPCODES_WARMUP_COUNT="$OPTARG" ;;
    f) FILTER="$OPTARG" ;;  # comma-separated exclude patterns
    *) echo "Usage: $0 [-t test_path] [-w warmup_file] [-c clients] [-r runs] [-i images] [-o opcodesWarmupCount] [-f filter]" >&2
       exit 1 ;;
  esac
done

IFS=',' read -ra CLIENT_ARRAY <<< "$CLIENTS"
IFS=',' read -ra FILTERS       <<< "$FILTER"

# Set up environment
rm -rf results
mkdir -p results
mkdir -p warmupresults
mkdir -p logs

# Initialize executions tracking
init_executions_file

# Install dependencies
pip install -r requirements.txt
make prepare_tools

# Find tests
TEST_FILES=()
while IFS= read -r -d '' file; do
  TEST_FILES+=("$file")
done < <(find "$TEST_PATH" -type f -name '*.txt' -print0)

# Run benchmarks
for run in $(seq 1 $RUNS); do
  for client in "${CLIENT_ARRAY[@]}"; do
    # Skip nimbus if already run today
    if [ "$client" = "nimbus" ] && was_executed_today "$client"; then
      echo "Skipping $client - already executed today"
      continue
    fi

    python3 -c "from utils import print_computer_specs; print(print_computer_specs())" \
    > results/computer_specs.txt
    cat results/computer_specs.txt

    # Setup node (with optional image override)
    if [ -z "$IMAGES" ]; then
      python3 setup_node.py --client $client
    else
      echo "Using provided image: $IMAGES for $client"
      python3 setup_node.py --client $client --imageBulk "$IMAGES"
    fi

    warmed=false

    # Warmup once per client/run
    if [ "$warmed" = false ]; then
      python3 run_kute.py --output warmupresults --testsPath "$WARMUP_FILE" --jwtPath /tmp/jwtsecret --client $client --run $run
      warmed=true
    fi

    for test_file in "${TEST_FILES[@]}"; do
      filename="${test_file##*/}"

      # Apply include-only filter if specified
      if [ -n "$FILTER" ]; then
        match=false
        for pat in "${FILTERS[@]}"; do
          if [[ -n "$pat" && "$filename" == *"$pat"* ]]; then
            match=true
            break
          fi
        done
        if ! $match; then
          echo "Skipping $filename (does not match include filter)"
          continue
        fi
      fi

      # Determine warmup path for this file
      warmup_filename="$(echo "$filename" | sed -E 's/_[0-9]+M/_150M/')"
      warmup_path="$WARMUP_OPCODES_PATH/$warmup_filename"

      # Opcodes warmup groups
      if (( OPCODES_WARMUP_COUNT > 0 )); then
        for warmup_count in $(seq 1 $OPCODES_WARMUP_COUNT); do
          python3 run_kute.py --output warmupresults --testsPath "$warmup_path" --jwtPath /tmp/jwtsecret --client $client --run $run --kuteArguments '-f engine_newPayloadV3'
        done
      fi

      # Actual measured run
      python3 run_kute.py --output results --testsPath "$test_file" --jwtPath /tmp/jwtsecret --client $client --run $run
      echo "" # Line break after each test for logs clarity
    done

    # Collect logs & teardown
    ts=$(date +%s)
    docker logs gas-execution-client 2> logs/docker_${client}_${ts}.log
    cl_name=$(echo "$client" | cut -d '_' -f 1)
    cd "scripts/$cl_name"
    docker compose down
    sudo rm -rf execution-data
    cd - >/dev/null

    update_execution_time "$client"
  done
done

# Process results
if [ -z "$IMAGES" ]; then
  python3 report_tables.py --resultsPath results --clients "$CLIENTS" --testsPath "$TEST_PATH" --runs $RUNS
  python3 report_html.py   --resultsPath results --clients "$CLIENTS" --testsPath "$TEST_PATH" --runs $RUNS
else
  python3 report_tables.py --resultsPath results --clients "$CLIENTS" --testsPath "$TEST_PATH" --runs $RUNS --images "$IMAGES"
  python3 report_html.py   --resultsPath results --clients "$CLIENTS" --testsPath "$TEST_PATH" --runs $RUNS --images "$IMAGES"
fi

# Prepare and zip the results
mkdir -p reports/docker
cp -r results/docker_* reports/docker
zip -r reports.zip reports
