# Prepare nethermind image that we will use on the script
cd scripts/nethermind-modexp

cp chainspec.json /tmp/chainspec.json
cp jwtsecret /tmp/jwtsecret

docker compose up -d

sleep 15

docker compose logs
