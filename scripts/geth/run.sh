# Prepare geth image that we will use on the script
cd scripts/geth
pwd
cp zkevmgenesis.json /tmp/genesis.json
cp jwtsecret /tmp/jwtsecret

docker compose up -d

sleep 15

docker compose logs
