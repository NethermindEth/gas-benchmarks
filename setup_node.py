# Create argument parser
import argparse
import datetime
import json
import os
import subprocess
import yaml

from utils import print_computer_specs


def run_command(client, run_path):
    # Add logic here to run the appropriate command for each client
    command = f'{run_path}/run.sh'
    print(f"{client} running at url 'http://localhost:8551'(auth), with command: '{command}'")
    subprocess.run(command, shell=True, text=True)


def set_image(client, el_images, run_path):
    if client == "nethermind":
        specifics = "CHAINSPEC_PATH=/tmp/chainspec.json"
    elif client == "besu":
        specifics = "CHAINSPEC_PATH=/tmp/besu.json"
        specifics += "\nEC_ENABLED_MODULES=ETH,NET,CLIQUE,DEBUG,MINER,NET,PERM,ADMIN,EEA,TXPOOL,PRIV,WEB3\n"
    else:
        specifics = "GENESIS_PATH=/tmp/genesis.json"
    env = f"EC_IMAGE_VERSION={el_images[client]}\n" \
          "EC_DATA_DIR=./execution-data\n" \
          "EC_JWT_SECRET_PATH=/tmp/jwtsecret\n" \
          f"{specifics}"

    env_file_path = os.path.join(run_path, ".env")
    if os.path.exists(env_file_path):
        os.remove(env_file_path)
    with open(env_file_path, "w") as file:
        file.write(env)


def main():
    parser = argparse.ArgumentParser(description='Benchmark script')
    parser.add_argument('--client', type=str, help='Client that we want to spin up.', default="nethermind")
    parser.add_argument('--image', type=str, help='Docker image of the client we are going to use.')
    parser.add_argument('--imageBulk', type=str, help='Docker image of the client we are going to use.',
                        default='{"nethermind": "default", "besu": "default", "geth": "default", "reth": "default"}, '
                        '"erigon": "default", "nimbus": "default"}')

    # Parse command-line arguments
    args = parser.parse_args()

    # Get client name and test case folder from command-line arguments
    client = args.client

    client_without_tag = client.split("_")[0]

    image = args.image
    images_bulk = args.imageBulk

    print(f'image Bulk: {images_bulk}')

    with open('images.yaml', 'r') as f:
        el_images = yaml.safe_load(f)["images"]

    if client_without_tag not in el_images:
        print("Client not supported")
        return

    images_json = json.loads(images_bulk)
    if images_json is not None:
        if client in images_json:
            if images_json[client] != 'default' and images_json[client] != '':
                el_images[client_without_tag] = images_json[client]

    if image is not None and image != 'default':
        el_images[client_without_tag] = image

    run_path = os.path.join(os.getcwd(), "scripts")
    run_path = os.path.join(run_path, client_without_tag)

    set_image(client_without_tag, el_images, run_path)

    # Start the client
    run_command(client, run_path)


if __name__ == '__main__':
    main()
