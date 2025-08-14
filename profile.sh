#!/bin/bash

BOLDGREEN="\e[32m"
ENDCOLOR="\e[0m"

echo -e "${BOLDGREEN}Remember, you can change the test to run under Makefile in run_single_benchmark${ENDCOLOR}"

echo "setting kernel.perf_event_max_sample_rate=100000"
sudo sysctl kernel.perf_event_max_sample_rate=100000

echo -e "${BOLDGREEN}Now running make run_single_benchmark${ENDCOLOR}"

rm -rf scripts/ethrex/profile.json.gz || true

cd ../ethrex
cargo build --profile release-with-debug --features metrics && cp -p target/release-with-debug/ethrex ../gas-benchmarks/scripts/ethrex
cd ../gas-benchmarks

bash run.sh -t "eest_tests" -g "zkevmgenesis.json" -w "" -c "ethrex" \
  -r 1 -i '{"nethermind":"default","geth":"default","reth":"default","erigon":"default","besu":"default","nimbus":"default","ethrex":"default"}' \
  -o "1" -f "bls12_g1add"
