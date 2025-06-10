#!/bin/bash

# Default inputs
TEST_PATH="tests/"
WARMUP_OPCODES_PATH="warmup-tests"
WARMUP_FILE="warmup/warmup-1000bl-16wi-24tx.txt"
CLIENTS="nethermind,geth,reth,besu,erigon"
RUNS=8
IMAGES='{"nethermind":"default","geth":"default","reth":"default","erigon":"default","besu":"default"}'
OPCODES_WARMUP_COUNT=1

# Parse command line arguments
while getopts "t:w:c:r:i:o:x" opt; do
  case $opt in
    t) TEST_PATH="$OPTARG" ;;
    w) WARMUP_FILE="$OPTARG" ;;
    c) CLIENTS="$OPTARG" ;;
    r) RUNS="$OPTARG" ;;
    i) IMAGES="$OPTARG" ;;
    o) OPCODES_WARMUP_COUNT="$OPTARG" ;;
    *) echo "Usage: $0 [-t test_path] [-w warmup_file] [-c clients] [-r runs] [-i images] [-o opcodesWarmupCount] [-x]" >&2
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

# Find tests
TEST_FILES=()
for file in $(find "$TEST_PATH" -type f -name '*.txt'); do
  TEST_FILES+=("$file")
done

# regenerate warmup scenarios in case of new tests added
python3 make_warmup_tests.py --source "$TEST_PATH" --dest "$WARMUP_OPCODES_PATH"
# Run benchmarks
for run in $(seq 1 $RUNS); do
  for client in "${CLIENT_ARRAY[@]}"; do
    warmed=false
    
    if [ -z "$IMAGES" ]; then
      python3 setup_node.py --client $client
    else
      echo "Using provided image: $IMAGES for $client"
      python3 setup_node.py --client $client --imageBulk "$IMAGES"
    fi    

    # Generic warmup
    if [ "$warmed" = "false" ]; then
      python3 run_kute.py --output warmupresults --testsPath "$WARMUP_FILE" --jwtPath /tmp/jwtsecret --client $client --run $run
      warmed=true
    fi
    
    for test_file in "${TEST_FILES[@]}"; do
      # Build the two separate paths:
      IFS='/' read -r -a parts <<< "$test_file"
      filename="${parts[${#parts[@]}-1]}"
      
      warmup_filename="$(echo "$filename" | sed -E 's/_[0-9]+M/_150M/')"
      warmup_path="$WARMUP_OPCODES_PATH/$warmup_filename"
      
      # Run warmup once on the batch
      for warmup_count in $(seq 1 $OPCODES_WARMUP_COUNT); do
        echo "Running warmup group: $prefix - warmup #$warmup_count"
        python3 run_kute.py --output warmupresults --testsPath "$warmup_path/Origin_150M.txt" --jwtPath /tmp/jwtsecret --client $client --run $run --kuteArguments "-f /.*(engine_newPayloadV3)/"
      done
      
      # Actual run
      echo 'Running measured scenarios...'
      python3 run_kute.py --output results --testsPath "$test_file/Origin_150M.txt" --jwtPath /tmp/jwtsecret --client $client --run $run
    done

    cl_name=$(echo "$client" | cut -d '_' -f 1)
    cd "scripts/$cl_name"
    docker compose down
    sudo rm -rf execution-data
    cd ../..
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
