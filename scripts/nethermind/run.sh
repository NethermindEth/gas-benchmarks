#!/bin/bash
set -e

# Prepare nethermind image that we will use on the script
cd scripts/nethermind

cp jwtsecret /tmp/jwtsecret

source ../common/wait_for_rpc.sh

docker compose up -d

wait_for_rpc "http://127.0.0.1:8545" 300

docker compose logs
