# Prepare nethermind image that we will use on the script
cd scripts/nethermind

cp zkevmchainspec.json /tmp/chainspec.json
cp jwtsecret /tmp/jwtsecret

docker compose up -d

sleep 15

docker compose logs
