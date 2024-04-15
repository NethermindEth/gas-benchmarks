#!/bin/bash

# Prepare geth image that we will use on the script
cd scripts/geth
pwd
cp genesis.json /tmp/genesis.json
cp jwtsecret /tmp/jwtsecret

docker compose up -d