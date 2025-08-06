import argparse
import datetime
import json
import os
import shutil
import subprocess
import yaml

from utils import print_computer_specs


def run_command(client, run_path):
    command = f"{run_path}/run.sh"
    print(
        f"{client} running at url 'http://localhost:8551'(auth), with command: '{command}'"
    )
    subprocess.run(command, shell=True, text=True)


def set_env(client, el_images, run_path):
    if "nethermind" in client:
        specifics = "CHAINSPEC_PATH=/tmp/chainspec.json"
    elif "besu" in client:
        specifics = "CHAINSPEC_PATH=/tmp/besu.json"
        specifics += "\nEC_ENABLED_MODULES=ETH,NET,CLIQUE,DEBUG,MINER,NET,PERM,ADMIN,TXPOOL,WEB3\n"
    else:
        specifics = "GENESIS_PATH=/tmp/genesis.json"

    env = (
        f"EC_IMAGE_VERSION={el_images[client]}\n"
        "EC_DATA_DIR=./execution-data\n"
        "EC_JWT_SECRET_PATH=/tmp/jwtsecret\n"
        f"{specifics}"
    )

    env_file_path = os.path.join(run_path, ".env")
    if os.path.exists(env_file_path):
        os.remove(env_file_path)
    with open(env_file_path, "w") as file:
        file.write(env)


def copy_genesis_file(client, genesis_path):
    target = None
    if "nethermind" in client:
        target = "/tmp/chainspec.json"
        default_source = f"scripts/{client}/chainspec.json"
    elif "besu" in client:
        target = "/tmp/besu.json"
        default_source = f"scripts/{client}/besu.json"
    else:
        target = "/tmp/genesis.json"
        default_source = f"scripts/{client}/genesis.json"

    source = genesis_path if genesis_path else default_source

    if not os.path.isfile(source):
        print(f"⚠️  Genesis file not found at: {source}, skipping copy")
        return

    try:
        shutil.copy(source, target)
        print(f"✅ Copied genesis file: {source} → {target}")
    except Exception as e:
        print(f"❌ Failed to copy genesis file from {source} to {target}: {e}")
        exit(1)


def main():
    parser = argparse.ArgumentParser(description="Benchmark script")
    parser.add_argument("--client", type=str, default="nethermind", help="Client to spin up")
    parser.add_argument("--image", type=str, help="Docker image override")
    parser.add_argument("--imageBulk", type=str, default='{"nethermind": "default", "besu": "default", "geth": "default", "reth": "default", "erigon": "default", "nimbus": "default", "ethrex": "default"}', help="Bulk image override")
    parser.add_argument("--genesisPath", type=str, help="Custom genesis file path")

    args = parser.parse_args()

    client = args.client
    client_without_tag = client.split("_")[0]

    image = args.image
    images_bulk = args.imageBulk
    genesis_path = args.genesisPath

    with open("images.yaml", "r") as f:
        el_images = yaml.safe_load(f)["images"]

    if client_without_tag not in el_images:
        print("❌ Client not supported:", client_without_tag)
        return

    # Override image from bulk if needed
    images_json = json.loads(images_bulk)
    if images_json and client in images_json:
        img = images_json[client]
        if img != "default" and img:
            el_images[client_without_tag] = img

    if image and image != "default":
        el_images[client_without_tag] = image

    run_path = os.path.join(os.getcwd(), "scripts", client_without_tag)

    # Copy custom genesis if provided
    copy_genesis_file(client_without_tag, genesis_path)

    # Prepare .env file
    set_env(client_without_tag, el_images, run_path)

    # Start client
    run_command(client, run_path)


if __name__ == "__main__":
    main()
