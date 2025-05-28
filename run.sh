#!/bin/bash

# Default inputs
TEST_PATH="tests/"
WARMUP_FILE="warmup/warmup-1000bl-16wi-24tx.txt"
CLIENTS="nethermind,geth,reth,besu,erigon"
RUNS=8
IMAGES='{"nethermind":"default","geth":"default","reth":"default","erigon":"default","besu":"default"}'
FILTER='150M'
OPCODES_WARMUP_COUNT=1

# Parse command line arguments
while getopts "t:w:c:r:i:f:o:x" opt; do
  case $opt in
    t) TEST_PATH="$OPTARG" ;;
    w) WARMUP_FILE="$OPTARG" ;;
    c) CLIENTS="$OPTARG" ;;
    r) RUNS="$OPTARG" ;;
    i) IMAGES="$OPTARG" ;;
    f) FILTER="$OPTARG" ;;
    o) OPCODES_WARMUP_COUNT="$OPTARG" ;;
    *) echo "Usage: $0 [-t test_path] [-w warmup_file] [-c clients] [-r runs] [-i images] [-f filter] [-o opcodesWarmupCount] [-x]" >&2
       exit 1 ;;
  esac
done

IFS=',' read -ra CLIENT_ARRAY <<< "$CLIENTS"

# Set up environment
mkdir -p results
mkdir -p warmupresults

# Install dependencies
pip install -r requirements.txt
make prepare_tools

# Find leaf directories
LEAF_DIRS=$(find "$TEST_PATH" -type d | while read -r dir; do
  if [ -z "$(find "$dir" -mindepth 1 -maxdepth 1 -type d)" ]; then
    echo "$dir"
  fi
done)

# Run benchmarks
for run in $(seq 1 $RUNS); do
  for client in "${CLIENT_ARRAY[@]}"; do
    for test_dir in $LEAF_DIRS; do
      if [ -z "$IMAGES" ]; then
        python3 setup_node.py --client $client
      else
        echo "Using provided image: $IMAGES for $client"
        python3 setup_node.py --client $client --imageBulk "$IMAGES"
      fi

      # Prepare temp dir for filtered scenarios (if needed)
      if [ -n "$FILTER" ]; then
        TMP_DIR=$(mktemp -d)
        echo "Filtering scenarios in $test_dir for '$FILTER'..."
        # Copy only matching scenario files (json/yaml)
        grep -Rl --include="*.txt" "$FILTER" "$test_dir" | while read -r src; do
          cp "$src" "$TMP_DIR"
        done
        TEST_DIR_TO_USE="$TMP_DIR"
      else
        TEST_DIR_TO_USE="$test_dir"
      fi

      # Warmup run
      for warmup_count in $(seq 1 $OPCODES_WARMUP_COUNT); do        
        echo 'Running warmup scenarios - warmup number: $warmup_count...'
        if [ -z "$WARMUP_FILE" ]; then
          python3 run_kute.py --output warmupresults --testsPath "$TEST_DIR_TO_USE" --jwtPath /tmp/jwtsecret --client $client --run $run
        else
          python3 run_kute.py --output warmupresults --testsPath "$TEST_DIR_TO_USE" --jwtPath /tmp/jwtsecret --warmupPath "$WARMUP_FILE" --client $client --run $run
        fi
      done
      
      # Actual run
      echo 'Running measured scenarios...'
      if [ -z "$WARMUP_FILE" ]; then
        python3 run_kute.py --output results --testsPath "$test_dir" --jwtPath /tmp/jwtsecret --client $client --run $run
      else
        python3 run_kute.py --output results --testsPath "$test_dir" --jwtPath /tmp/jwtsecret --warmupPath "$WARMUP_FILE" --client $client --run $run
      fi

      cl_name=$(echo "$client" | cut -d '_' -f 1)
      cd "scripts/$cl_name"
      docker compose down
      sudo rm -rf execution-data
      cd ../..
    done
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
mkdir -p reports/docker
cp -r results/docker_* reports/docker
zip -r reports.zip reports
