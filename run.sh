#!/bin/bash

# Default inputs
TEST_PATH="tests/"
WARMUP_FILE="warmup/warmup-1000bl-16wi-24tx.txt"
CLIENTS="nethermind,geth,reth"
RUNS=8
IMAGES="default"

# Parse command line arguments
while getopts "t:w:c:r:i:" opt; do
  case $opt in
    t) TEST_PATH="$OPTARG" ;;
    w) WARMUP_FILE="$OPTARG" ;;
    c) CLIENTS="$OPTARG" ;;
    r) RUNS="$OPTARG" ;;
    i) IMAGES="$OPTARG" ;;
    *) echo "Usage: $0 [-t test_path] [-w warmup_file] [-c clients] [-r runs] [-i images]" >&2
       exit 1 ;;
  esac
done

IFS=',' read -ra CLIENT_ARRAY <<< "$CLIENTS"
IFS=',' read -ra IMAGE_ARRAY <<< "$IMAGES"

# Set up environment
mkdir -p results

# Install dependencies
pip install -r requirements.txt
make prepare_tools

# Run benchmarks
for run in $(seq 1 $RUNS); do
  for i in "${!CLIENT_ARRAY[@]}"; do
    client="${CLIENT_ARRAY[$i]}"
    image="${IMAGE_ARRAY[$i]}"

    if [ -z "$image" ]; then
      echo "Image input is empty, using default image."
      python3 setup_node.py --client $client
    else
      echo "Using provided image: $image for $client"
      python3 setup_node.py --client $client --image $image
    fi

    if [ -z "$WARMUP_FILE" ]; then
      echo "Running script without warm up."
      python3 run_kute.py --output results --testsPath "$TEST_PATH" --jwtPath /tmp/jwtsecret --client $client --run $run
    else
      echo "Using provided warm up file: $WARMUP_FILE"
      python3 run_kute.py --output results --testsPath "$TEST_PATH" --jwtPath /tmp/jwtsecret --warmupPath "$WARMUP_FILE" --client $client --run $run
    fi

    cd "scripts/$client"
    docker compose down
    sudo rm -rf execution-data
    cd ../..
  done
done

# Get metrics from results
python3 report_tables.py --resultsPath results --clients "$CLIENTS" --testsPath "$TEST_PATH" --runs $RUNS
python3 report_html.py --resultsPath results --clients "$CLIENTS" --testsPath "$TEST_PATH" --runs $RUNS


# Zip the results folder
zip -r results.zip results