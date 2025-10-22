#!/bin/bash
set -e

# Prepare besu image that we will use on the script
cd scripts/besu

cp jwtsecret /tmp/jwtsecret

source ../common/wait_for_rpc.sh

docker compose up -d

wait_for_rpc "http://127.0.0.1:8545"

docker compose logs
