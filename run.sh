#!/bin/bash

# Default inputs
TEST_PATH="tests-vm/"
WARMUP_FILE=""
CLIENTS="nethermind"
RUNS=1
IMAGES='{"nethermind":"default","geth":"default","reth":"default","erigon":"default","besu":"default"}'

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
mkdir -p results

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

      if [ -z "$WARMUP_FILE" ]; then
        echo "Running script without warm up."
        python3 run_kute.py --output results --testsPath "$test_dir/Origin_150M.txt" --jwtPath /tmp/jwtsecret --client $client --run $run
      else
        echo "Using provided warm up file: $WARMUP_FILE"
        python3 run_kute.py --output results --testsPath "$test_dir/Origin_150M.txt" --jwtPath /tmp/jwtsecret --warmupPath "$WARMUP_FILE" --client $client --run $run
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
