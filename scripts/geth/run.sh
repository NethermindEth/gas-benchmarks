#!/bin/bash
set -e

# Prepare geth image that we will use on the script
cd scripts/geth

cp jwtsecret /tmp/jwtsecret

source ../common/wait_for_rpc.sh
source ../common/docker_compose.sh

compose_cmd up --detach

wait_for_rpc "http://127.0.0.1:8545"

compose_cmd logs
