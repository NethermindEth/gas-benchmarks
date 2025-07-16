#!/bin/bash

# Default inputs
WARMUP_OPCODES_PATH="warmup-tests"
WARMUP_FILE=""
CLIENTS="besu"
RUNS=1
OPCODES_WARMUP_COUNT=2
FILTER="keccak"
IMAGES='{"nethermind":"default","geth":"default","reth":"default","erigon":"default","besu":"default","nimbus":"default"}'
EXECUTIONS_FILE="executions.json"
TEST_PATHS_JSON=""
LEGACY_TEST_PATH="tests-vm"
LEGACY_GENESIS_PATH=""

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

# Parse command line arguments
while getopts "T:t:g:w:c:r:i:o:f:" opt; do
  case $opt in
    T) TEST_PATHS_JSON="$OPTARG" ;;
    t) LEGACY_TEST_PATH="$OPTARG" ;;
    g) LEGACY_GENESIS_PATH="$OPTARG" ;;
    w) WARMUP_FILE="$OPTARG" ;;
    c) CLIENTS="$OPTARG" ;;
    r) RUNS="$OPTARG" ;;
    i) IMAGES="$OPTARG" ;;
    o) OPCODES_WARMUP_COUNT="$OPTARG" ;;
    f) FILTER="$OPTARG" ;;
    *) echo "Usage: $0 [-T test_paths_json] [-t test_path] [-g genesis_path] [-w warmup_file] [-c clients] [-r runs] [-i images] [-o opcodesWarmupCount] [-f filter]" >&2
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

# Setup environment
rm -rf results
mkdir -p results warmupresults logs

init_executions_file
end_timer "executions_init"

pip install -r requirements.txt
make prepare_tools
end_timer "dependencies_install"

# Find test files and their associated genesis paths
TEST_FILES=()
TEST_TO_GENESIS=()

for i in "${!TEST_PATHS[@]}"; do
  path="${TEST_PATHS[$i]}"
  genesis="${GENESIS_PATHS[$i]}"
  while IFS= read -r -d '' file; do
    TEST_FILES+=("$file")
    TEST_TO_GENESIS+=("$genesis")
  done < <(find "$path" -type f -name '*.txt' -print0)
done

# Run benchmarks
start_timer "benchmarks_total"
for run in $(seq 1 $RUNS); do
  debug_log "Starting run $run/$RUNS"
  for client in "${CLIENT_ARRAY[@]}"; do
    if [ "$client" = "nimbus" ] && was_executed_today "$client"; then
      echo "Skipping $client - already executed today"
      continue
    fi

    raw_genesis="${TEST_TO_GENESIS[$i]}"
    cl_name=$(echo "$client" | cut -d '_' -f 1)
    
    if [ -n "$raw_genesis" ]; then
      genesis_path="scripts/$cl_name/$raw_genesis"
    else
      genesis_path=""
    fi

    # Setup node
    if [ -n "$genesis_path" ]; then
      echo "Using custom genesis for $client: $genesis_path"
      python3 setup_node.py --client "$client" --imageBulk "$IMAGES" --genesisPath "$genesis_path"
    else
      python3 setup_node.py --client "$client" --imageBulk "$IMAGES"
    fi
    end_timer "setup_node_${client}"

    python3 -c "from utils import print_computer_specs; print(print_computer_specs())" > results/computer_specs.txt
    cat results/computer_specs.txt

    warmed=false

    # Warmup
    if [ "$warmed" = false ]; then
      python3 run_kute.py --output warmupresults --testsPath "$WARMUP_FILE" --jwtPath /tmp/jwtsecret --client "$client" --run "$run"
      warmed=true
      end_timer "warmup_${client}_run_${run}"
    fi

    for i in "${!TEST_FILES[@]}"; do
      test_file="${TEST_FILES[$i]}"
      filename="${test_file##*/}"

      if [ -n "$FILTER" ]; then
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

      warmup_filename="$(echo "$filename" | sed -E 's/_[0-9]+M/_150M/')"
      warmup_path="$WARMUP_OPCODES_PATH/$warmup_filename"

      if (( OPCODES_WARMUP_COUNT > 0 )); then
        start_test_timer "opcodes_warmup_${client}_${filename}"
        for warmup_count in $(seq 1 $OPCODES_WARMUP_COUNT); do
          python3 run_kute.py --output warmupresults --testsPath "$warmup_path" --jwtPath /tmp/jwtsecret --client "$client" --run "$run" --kuteArguments '-f engine_newPayload'
        done
        end_test_timer "opcodes_warmup_${client}_${filename}"
      fi

      python3 run_kute.py --output results --testsPath "$test_file" --jwtPath /tmp/jwtsecret --client "$client" --run "$run"
      echo ""
    done

    ts=$(date +%s)
    docker logs gas-execution-client 2> logs/docker_${client}_${ts}.log

    cl_name=$(echo "$client" | cut -d '_' -f 1)
    cd "scripts/$cl_name"
    docker compose down
    rm -rf execution-data
    cd - >/dev/null
    end_timer "teardown_${client}"

    update_execution_time "$client"
    end_timer "client_${client}_run_${run}"
  done
done
end_timer "benchmarks_total"

# Generate report
if [ -z "$IMAGES" ]; then
  python3 report_tables.py --resultsPath results --clients "$CLIENTS" --testsPath "${TEST_PATHS[0]}" --runs "$RUNS"
  python3 report_html.py   --resultsPath results --clients "$CLIENTS" --testsPath "${TEST_PATHS[0]}" --runs "$RUNS"
else
  python3 report_tables.py --resultsPath results --clients "$CLIENTS" --testsPath "${TEST_PATHS[0]}" --runs "$RUNS" --images "$IMAGES"
  python3 report_html.py   --resultsPath results --clients "$CLIENTS" --testsPath "${TEST_PATHS[0]}" --runs "$RUNS" --images "$IMAGES"
fi
end_timer "results_processing"

# Package results
mkdir -p reports/docker
cp -r results/docker_* reports/docker
zip -r reports.zip reports
end_timer "results_packaging"

# Print timing summary at the end
print_timing_summary
