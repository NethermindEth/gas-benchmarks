import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
import time
from typing import Any, Dict, Optional

import yaml

from utils import print_computer_specs

REPO_ROOT = Path(__file__).resolve().parent

GENESIS_FILES: Dict[str, Path] = {
    "nethermind": REPO_ROOT / "scripts" / "genesisfiles" / "nethermind" / "zkevmgenesis.json",
    "besu": REPO_ROOT / "scripts" / "genesisfiles" / "besu" / "zkevmgenesis.json",
    "geth": REPO_ROOT / "scripts" / "genesisfiles" / "geth" / "zkevmgenesis.json",
    "reth": REPO_ROOT / "scripts" / "genesisfiles" / "geth" / "zkevmgenesis.json",
    "erigon": REPO_ROOT / "scripts" / "genesisfiles" / "geth" / "zkevmgenesis.json",
    "nimbus": REPO_ROOT / "scripts" / "genesisfiles" / "geth" / "zkevmgenesis.json",
    "ethrex": REPO_ROOT / "scripts" / "genesisfiles" / "geth" / "zkevmgenesis.json",
}
DEFAULT_GENESIS = GENESIS_FILES["geth"]


def resolve_nethermind_config_network(network: str) -> str:
    normalized = str(network).strip().lower()
    if normalized in {"mainnet", "perf-devnet-2"}:
        return "mainnet"
    return normalized


CLIENT_METADATA: Dict[str, Dict[str, Any]] = {
    "nethermind": {
        "env_key": "CHAINSPEC_PATH",
        "default_source": GENESIS_FILES["nethermind"],
        "target": Path("/tmp/chainspec.json"),
        "flags": [
            {
                "env": "NETHERMIND_CONFIG_FLAG",
                "custom": "--config=none",
                "network": lambda net: f"--config={resolve_nethermind_config_network(net)}",
            },
            {
                "env": "NETHERMIND_GENESIS_FLAG",
                "custom": "--Init.ChainSpecPath=/tmp/chainspec/chainspec.json",
                "network": "",
            },
        ],
        "extra_env": {},
    },
    "besu": {
        "env_key": "CHAINSPEC_PATH",
        "default_source": GENESIS_FILES["besu"],
        "target": Path("/tmp/besu.json"),
        "flags": [
            {
                "env": "BESU_GENESIS_FLAG",
                "custom": "--genesis-file=/tmp/chainspec/chainspec.json",
                "network": "",
            },
            {
                "env": "BESU_NETWORK_FLAG",
                "custom": "",
                "network": lambda net: f"--network={net.lower()}",
            },
        ],
        "extra_env": {
            "EC_ENABLED_MODULES": "ETH,NET,CLIQUE,DEBUG,MINER,NET,PERM,ADMIN,TXPOOL,WEB3",
        },
    },
    "geth": {
        "env_key": "GENESIS_PATH",
        "default_source": GENESIS_FILES["geth"],
        "target": Path("/tmp/genesis.json"),
        "flags": [
            {
                "env": "GETH_NETWORK_FLAG",
                "custom": "--override.genesis=/tmp/genesis/genesis.json",
                "network": lambda net: f"--{net.lower()}",
            },
            {
                "env": "GETH_INIT_COMMAND",
                "custom": "geth init --datadir=/var/lib/goethereum /tmp/genesis/genesis.json",
                "network": "",
            },
        ],
        "extra_env": {},
    },
    "reth": {
        "env_key": "GENESIS_PATH",
        "default_source": GENESIS_FILES["reth"],
        "target": Path("/tmp/genesis.json"),
        "flags": [
            {
                "env": "RETH_CHAIN_ARG",
                "custom": "--chain=/tmp/genesis/genesis.json",
                "network": lambda net: f"--chain={net.lower()}",
            },
            {
                "env": "RETH_INIT_COMMAND",
                "custom": "/usr/local/bin/reth init --datadir /var/lib/reth --chain /tmp/genesis/genesis.json",
                "network": "",
            },
        ],
        "extra_env": {},
    },
    "erigon": {
        "env_key": "GENESIS_PATH",
        "default_source": GENESIS_FILES["erigon"],
        "target": Path("/tmp/genesis.json"),
        "flags": [
            {
                "env": "ERIGON_CHAIN_FLAG",
                "custom": "--chain=/tmp/genesis/genesis.json",
                "network": lambda net: f"--chain={net.lower()}",
            },
            {
                "env": "ERIGON_INIT_COMMAND",
                "custom": "erigon init --datadir=/var/lib/erigon /tmp/genesis/genesis.json",
                "network": "",
            },
        ],
        "extra_env": {},
    },
    "nimbus": {
        "env_key": "GENESIS_PATH",
        "default_source": GENESIS_FILES["nimbus"],
        "target": Path("/tmp/genesis.json"),
        "flags": [
            {
                "env": "NIMBUS_NETWORK_FLAG",
                "custom": "--custom-network=/tmp/genesis/genesis.json",
                "network": lambda net: f"--network={net.lower()}",
            },
        ],
        "extra_env": {},
    },
    "ethrex": {
        "env_key": "GENESIS_PATH",
        "default_source": GENESIS_FILES["ethrex"],
        "target": Path("/tmp/genesis.json"),
        "flags": [
            {
                "env": "ETHREX_NETWORK_FLAG",
                "custom": "--network=/tmp/genesis/genesis.json",
                "network": lambda net: f"--network={net.lower()}",
            },
        ],
        "extra_env": {},
    },
}

DEFAULT_CLIENT_METADATA: Dict[str, Any] = {
    "env_key": "GENESIS_PATH",
    "default_source": DEFAULT_GENESIS,
    "target": Path("/tmp/genesis.json"),
    "flags": [],
    "extra_env": {},
}


def sanitize_volume_name(name: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)
    return sanitized or "gasbench_volume"


def run_command(client, run_path):
    command = f"{run_path}/run.sh"
    print(
        f"{client} running at url 'http://localhost:8551'(auth), with command: '{command}'",
        flush=True,
    )
    completed = subprocess.run(command, shell=True, text=True, check=False, cwd=run_path)
    if completed.returncode != 0:
        print(
            f"ERROR: Client startup script failed with exit code {completed.returncode}",
            flush=True,
        )
        raise SystemExit(completed.returncode)


def get_metadata(client: str) -> Dict[str, Any]:
    return CLIENT_METADATA.get(client, DEFAULT_CLIENT_METADATA)


def evaluate_flag(flag_entry: Dict[str, Any], network: Optional[str], use_custom_genesis: bool) -> str:
    key = "custom" if use_custom_genesis else "network"
    value = flag_entry.get(key, "")
    if callable(value):
        if network is None:
            return ""
        return value(network)
    return value or ""


INIT_SKIP_ON_SNAPSHOT_BACKEND: Dict[str, Dict[str, str]] = {
    "geth": {"GETH_INIT_COMMAND": "true"},
}


def _is_snapshot_clone_path(candidate: Optional[str]) -> bool:
    if not candidate:
        return False
    try:
        resolved = Path(candidate).resolve()
    except Exception:
        return False
    lowercase_parts = [part.lower() for part in resolved.parts]
    if "merged" in lowercase_parts and any("overlay" in part for part in lowercase_parts):
        return True
    return any("gasbench-runtime" in part for part in lowercase_parts)


def set_env(
    client: str,
    el_images: Dict[str, str],
    run_path: str,
    data_dir: Optional[str],
    data_backend: Optional[str],
    network: Optional[str],
    use_custom_genesis: bool,
    genesis_host_path: Path,
    metadata: Dict[str, Any],
    volume_name: Optional[str],
):
    run_path_obj = Path(run_path).resolve()
    if not run_path_obj.is_dir():
        raise FileNotFoundError(f"Client run path does not exist: {run_path_obj}")

    resolved_data_dir = Path(data_dir or run_path_obj / "execution-data").resolve()

    env_map: Dict[str, str] = {
        "EC_IMAGE_VERSION": el_images[client],
        "EC_DATA_DIR": resolved_data_dir.as_posix(),
        "EC_JWT_SECRET_PATH": "/tmp/jwtsecret",
        metadata["env_key"]: genesis_host_path.as_posix(),
        "USE_CUSTOM_GENESIS": "true" if use_custom_genesis else "false",
        "NETWORK_NAME": network or "",
        "EC_DATA_BACKEND": (data_backend or "direct"),
    }

    sanitized_volume = sanitize_volume_name(volume_name) if volume_name else sanitize_volume_name(
        f"{client}_{int(time.time())}"
    )
    env_map["EC_VOLUME_NAME"] = sanitized_volume

    for flag_entry in metadata.get("flags", []):
        env_key = flag_entry.get("env")
        if not env_key:
            continue
        evaluated = evaluate_flag(flag_entry, network, use_custom_genesis)
        if evaluated:
            env_map[env_key] = evaluated

    for extra_key, extra_value in metadata.get("extra_env", {}).items():
        env_map[extra_key] = extra_value

    normalized_backend = (data_backend or "").strip().lower()
    is_snapshot_clone = normalized_backend in {"overlay", "zfs"} or _is_snapshot_clone_path(data_dir)
    if is_snapshot_clone:
        overrides = INIT_SKIP_ON_SNAPSHOT_BACKEND.get(client, {})
        for env_key, override in overrides.items():
            env_map[env_key] = override

    env_lines = [f"{key}={value}" for key, value in env_map.items()]

    env_file_path = run_path_obj / ".env"
    if env_file_path.exists():
        env_file_path.unlink()
    with open(env_file_path, "w", encoding="utf-8") as file:
        file.write("\n".join(env_lines))


def copy_genesis_file(source: Path, target: Path) -> None:
    if not source.is_file():
        print(f"[error] Genesis file not found at: {source}")
        exit(1)

    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copy(source, target)
        print(f"[info] Copied genesis file: {source} -> {target}")
    except Exception as exc:
        print(f"[error] Failed to copy genesis file from {source} to {target}: {exc}")
        exit(1)


def main():
    parser = argparse.ArgumentParser(description="Benchmark script")
    parser.add_argument("--client", type=str, default="nethermind", help="Client to spin up")
    parser.add_argument("--image", type=str, help="Docker image override")
    parser.add_argument(
        "--imageBulk",
        type=str,
        default='{"nethermind": "default", "besu": "default", "geth": "default", "reth": "default", "erigon": "default", "nimbus": "default", "ethrex": "default"}',
        help="Bulk image override",
    )
    parser.add_argument("--genesisPath", type=str, help="Custom genesis file path")
    parser.add_argument("--network", type=str, help="Named network to resolve default genesis")
    parser.add_argument(
        "--dataDir",
        type=str,
        help="Host directory to bind into the client as data dir",
    )
    parser.add_argument(
        "--dataBackend",
        type=str,
        choices=["direct", "overlay", "zfs"],
        default="direct",
        help="Data directory backend mode (direct, overlay, zfs)",
    )
    parser.add_argument(
        "--volumeName",
        type=str,
        help="Docker volume name override",
    )

    args = parser.parse_args()

    client = args.client
    client_without_tag = client.split("_")[0]

    image = args.image
    images_bulk = args.imageBulk
    genesis_path = args.genesisPath
    network = args.network
    data_dir = args.dataDir
    data_backend = args.dataBackend
    volume_name = args.volumeName

    with open(REPO_ROOT / "images.yaml", "r", encoding="utf-8") as f:
        el_images = yaml.safe_load(f)["images"]

    if client_without_tag not in el_images:
        print("[error] Client not supported:", client_without_tag)
        return

    # Override image from bulk if needed
    images_json = json.loads(images_bulk)
    if images_json and client in images_json:
        img = images_json[client]
        if img != "default" and img:
            el_images[client_without_tag] = img

    if image and image != "default":
        el_images[client_without_tag] = image

    run_path = str((REPO_ROOT / "scripts" / client_without_tag).resolve())

    metadata = get_metadata(client_without_tag)
    use_custom_genesis = bool(genesis_path)

    if network and use_custom_genesis:
        print(f"[info] Using custom genesis with network context: {network}")

    genesis_target: Path = metadata["target"]
    if use_custom_genesis:
        source = Path(genesis_path).resolve() if genesis_path else metadata["default_source"].resolve()
        copy_genesis_file(source, genesis_target)
    else:
        genesis_target.parent.mkdir(parents=True, exist_ok=True)
        genesis_target.touch(exist_ok=True)

    # Prepare .env file
    set_env(
        client=client_without_tag,
        el_images=el_images,
        run_path=run_path,
        data_dir=data_dir,
        data_backend=data_backend,
        network=network,
        use_custom_genesis=use_custom_genesis,
        genesis_host_path=genesis_target,
        metadata=metadata,
        volume_name=volume_name,
    )

    # Start client
    run_command(client, run_path)


if __name__ == "__main__":
    main()

