# Prepare ethrex image that we will use on the script
cd scripts/ethrex

cp jwtsecret /tmp/jwtsecret

samply record -r 50000 -s -n ./ethrex --network="/tmp/genesis.json" --datadir="$(pwd)/execution-data" \
  --metrics.addr=0.0.0.0 \
  --metrics.port=8008 \
  --authrpc.jwtsecret="/tmp/jwtsecret" \
  --http.addr=0.0.0.0 \
  --http.port=8545 \
  --authrpc.addr=0.0.0.0 >../../logs/ethrex-logs.txt 2>&1 &

sleep 14

tail ../../logs/ethrex-logs.txt
