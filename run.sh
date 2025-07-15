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
DEBUG=false
DEBUG_FILE=""
PROFILE_TEST=false

# Timing variables
declare -A STEP_TIMES
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
while getopts "t:w:c:r:i:o:f:dD:p" opt; do
  case $opt in
    t) TEST_PATH="$OPTARG" ;;
    w) WARMUP_FILE="$OPTARG" ;;
    c) CLIENTS="$OPTARG" ;;
    r) RUNS="$OPTARG" ;;
    i) IMAGES="$OPTARG" ;;
    o) OPCODES_WARMUP_COUNT="$OPTARG" ;;
    f) FILTER="$OPTARG" ;;  # comma-separated exclude patterns
    d) DEBUG=true ;;
    D) DEBUG=true; DEBUG_FILE="$OPTARG" ;;
    p) PROFILE_TEST=true ;;
    *) echo "Usage: $0 [-t test_path] [-w warmup_file] [-c clients] [-r runs] [-i images] [-o opcodesWarmupCount] [-f filter] [-d debug] [-D debug_file] [-p profile_test]" >&2
       exit 1 ;;
  esac
done

IFS=',' read -ra CLIENT_ARRAY <<< "$CLIENTS"
IFS=',' read -ra FILTERS       <<< "$FILTER"

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

# Set up environment
start_timer "environment_setup"
rm -rf results
mkdir -p results
mkdir -p warmupresults
mkdir -p logs
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

# Find tests
start_timer "test_discovery"
TEST_FILES=()
while IFS= read -r -d '' file; do
  TEST_FILES+=("$file")
done < <(find "$TEST_PATH" -type f -name '*.txt' -print0)
debug_log "Found ${#TEST_FILES[@]} test files"
end_timer "test_discovery"

# Run benchmarks
start_timer "benchmarks_total"
for run in $(seq 1 $RUNS); do
  debug_log "Starting run $run/$RUNS"
  for client in "${CLIENT_ARRAY[@]}"; do
    debug_log "Processing client: $client"
    
    # Skip nimbus if already run today
    if [ "$client" = "nimbus" ] && was_executed_today "$client"; then
      echo "Skipping $client - already executed today"
      continue
    fi

    python3 -c "from utils import print_computer_specs; print_computer_specs()" \
    > results/computer_specs.txt
    cat results/computer_specs.txt
    end_timer "computer_specs"

    # Setup node (with optional image override)
    start_timer "setup_node_${client}"
    if [ -z "$IMAGES" ]; then
      python3 setup_node.py --client $client
    else
      echo "Using provided image: $IMAGES for $client"
      python3 setup_node.py --client $client --imageBulk "$IMAGES"
    fi
    end_timer "setup_node_${client}"

    warmed=false

    # Warmup once per client/run
    if [ "$warmed" = false ]; then
      start_timer "warmup_${client}_run_${run}"
      python3 run_kute.py --output warmupresults --testsPath "$WARMUP_FILE" --jwtPath /tmp/jwtsecret --client $client --run $run
      warmed=true
      end_timer "warmup_${client}_run_${run}"
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
        start_test_timer "opcodes_warmup_${client}_${filename}"
        for warmup_count in $(seq 1 $OPCODES_WARMUP_COUNT); do
          test_debug_log "Opcodes warmup $warmup_count/$OPCODES_WARMUP_COUNT for $filename"
          python3 run_kute.py --output warmupresults --testsPath "$warmup_path" --jwtPath /tmp/jwtsecret --client $client --run $run --kuteArguments '-f engine_newPayloadV3'
        done
        end_test_timer "opcodes_warmup_${client}_${filename}"
      fi

      # Actual measured run
      start_test_timer "test_run_${client}_${filename}"
      test_debug_log "Running test: $filename"
      python3 run_kute.py --output results --testsPath "$test_file" --jwtPath /tmp/jwtsecret --client $client --run $run
      end_test_timer "test_run_${client}_${filename}"
      echo "" # Line break after each test for logs clarity
    done

    # Collect logs & teardown
    start_timer "teardown_${client}"
    ts=$(date +%s)
    docker logs gas-execution-client &> logs/docker_${client}_${ts}.log
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

# Process results
start_timer "results_processing"
if [ -z "$IMAGES" ]; then
  python3 report_tables.py --resultsPath results --clients "$CLIENTS" --testsPath "$TEST_PATH" --runs $RUNS
  python3 report_html.py   --resultsPath results --clients "$CLIENTS" --testsPath "$TEST_PATH" --runs $RUNS
else
  python3 report_tables.py --resultsPath results --clients "$CLIENTS" --testsPath "$TEST_PATH" --runs $RUNS --images "$IMAGES"
  python3 report_html.py   --resultsPath results --clients "$CLIENTS" --testsPath "$TEST_PATH" --runs $RUNS --images "$IMAGES"
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
