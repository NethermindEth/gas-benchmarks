#!/usr/bin/env python3
import argparse
import base64
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path
import atexit
import re

CHAIN_TO_ID = {
    "mainnet": 1,
    "ethereum": 1,
    "sepolia": 11155111,
    "holesky": 17000,
    "goerli": 5,
}

CLEANUP = {"keep": False, "container": None, "merged": None, "upper": None, "work": None, "mitm": None}

def run(cmd, cwd=None, env=None, check=True):
    print("\n[RUN] " + " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, env=env, check=check)

def check_cmd_exists(cmd): return shutil.which(cmd) is not None

def wait_for_port(host, port, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(1)
    return False

def ensure_pip_pkg(pkg):
    if pkg == "mitmproxy" and shutil.which(pkg): return
    try:
        __import__(pkg)
    except Exception:
        run([sys.executable, "-m", "pip", "install", "-U", pkg])

def rpc_call(url, method, params=None, headers=None, timeout=10):
    import requests
    body = {"jsonrpc": "2.0", "id": int(time.time()), "method": method, "params": params or []}
    r = requests.post(url, json=body, headers=headers or {}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if "error" in data: raise RuntimeError(f"RPC error from {url}: {data['error']}")
    return data.get("result")

def _atomic_write_json(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2); f.write("\n")
    tmp.replace(path)
    print(f"[SAVE] {path}")

def _next_indexed_path(base_dir: Path, prefix: str) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)\.json$")
    max_n = 0
    for p in base_dir.glob(f"{prefix}-*.json"):
        m = pattern.match(p.name)
        if m:
            try: max_n = max(max_n, int(m.group(1)))
            except ValueError: pass
    return base_dir / f"{prefix}-{max_n + 1}.json"

def is_mounted(mount_point: Path) -> bool:
    try:
        with open("/proc/mounts", "r") as f:
            return any(line.split()[1] == str(mount_point.resolve()) for line in f)
    except Exception:
        return False

def ensure_overlay_mount(lower: Path, upper: Path, work: Path, merged: Path):
    lower = lower.resolve(); upper = upper.resolve(); work = work.resolve(); merged = merged.resolve()
    if not lower.exists() or not any(lower.iterdir()):
        raise RuntimeError(f"Lower dir {lower} missing or empty; download snapshot first.")
    upper.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    merged.mkdir(parents=True, exist_ok=True)
    mount_opts = f"lowerdir={lower},upperdir={upper},workdir={work}"
    cmd = ["mount", "-t", "overlay", "overlay", "-o", mount_opts, str(merged)]
    if hasattr(os, "geteuid") and os.geteuid() != 0 and shutil.which("sudo"):
        cmd = ["sudo"] + cmd
    run(cmd)

def unmount_overlay(merged: Path):
    if not is_mounted(merged): return
    cmd = ["umount", str(merged)]
    if hasattr(os, "geteuid") and os.geteuid() != 0 and shutil.which("sudo"):
        cmd = ["sudo"] + cmd
    subprocess.run(cmd, check=False)

def download_snapshot(chain: str, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    sh = textwrap.dedent(f"""
        apk add --no-cache curl tar zstd >/dev/null && \
        BLOCK_NUMBER=$(curl -s https://snapshots.ethpandaops.io/{chain}/nethermind/latest) && \
        curl -s -L https://snapshots.ethpandaops.io/{chain}/nethermind/$BLOCK_NUMBER/snapshot.tar.zst | \
        tar -I zstd -xvf - -C /data --strip-components=1
    """)
    cmd = [
        "docker", "run", "--rm", "-i",
        "-v", f"{str(out_dir.resolve())}:/data",
        "--entrypoint", "/bin/sh",
        "alpine", "-c", sh,
    ]
    run(cmd)

def ensure_jwt(jwt_dir: Path) -> Path:
    jwt_dir.mkdir(parents=True, exist_ok=True)
    jwt = jwt_dir / "jwt.hex"
    if not jwt.exists(): jwt.write_text(os.urandom(32).hex())
    return jwt

def start_nethermind_container(chain: str, db_dir: Path, jwt_path: Path,
                               rpc_port=8545, engine_port=8551, name="eest-nethermind") -> str:
    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "-p", f"{rpc_port}:{rpc_port}",
        "-p", f"{engine_port}:{engine_port}",
        "-v", f"{str(db_dir.resolve())}:/db",
        "-v", f"{str(jwt_path.parent.resolve())}:/jwt:ro",
        "nethermindeth/nethermind:gp-hacked",
        "--config", str(chain),
        "--JsonRpc.Enabled", "true",
        "--JsonRpc.Host", "0.0.0.0",
        "--JsonRpc.Port", str(rpc_port),
        "--JsonRpc.EngineHost", "0.0.0.0",
        "--JsonRpc.EnginePort", str(engine_port),
        "--JsonRpc.JwtSecretFile", "/jwt/jwt.hex",
        "--JsonRpc.UnsecureDevNoRpcAuthentication", "true",
        "--JsonRpc.EnabledModules", "Eth,Net,Web3,Admin,Debug,Trace,TxPool",
        "--Blocks.TargetBlockGasLimit", "1000000000000",
        "--data-dir", "/db",
        "--log", "DEBUG",
        "--Network.MaxActivePeers", "0",
        "--TxPool.Size", "10000",
        "--TxPool.MaxTxSize", "null",
    ]
    cp = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, text=True)
    return cp.stdout.strip()

def _container_exists(name: str) -> bool:
    try:
        cp = subprocess.run(["docker", "inspect", "--format", "{{.Name}}", name], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
        return cp.returncode == 0
    except Exception:
        return False

def stop_and_remove_container(name: str):
    subprocess.run(["docker", "rm", "-f", name], check=False)

def print_container_logs(name: str):
    if not _container_exists(name):
        return
    try:
        out = subprocess.run(["docker", "logs", name], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
        Path("/root/nethermind.log").write_text(out.stdout, encoding="utf-8")
    except Exception:
        pass

def start_mitm_proxy(addon_path: Path, listen_port=8549, upstream="http://127.0.0.1:8545"):
    env = os.environ.copy()
    env["MITM_ADDON_CONFIG"] = str(Path("mitm_config.json").resolve())
    cmd = ["mitmdump", "-p", str(listen_port), "--mode", f"reverse:{upstream}", "-s", str(addon_path), "--set", "connection_strategy=lazy", "--set", "http2=false"]
    print("\n[RUN] " + " ".join(cmd))
    return subprocess.Popen(cmd, env=env)

def _engine_with_jwt(engine_url: str, jwt_hex_path: Path, method: str, params: list, timeout=30):
    import requests, hmac, hashlib
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b'=')
    payload = base64.urlsafe_b64encode(json.dumps({"iat": int(time.time())}).encode()).rstrip(b'=')
    unsigned = header + b"." + payload
    secret_hex = Path(jwt_hex_path).read_text().strip().replace("0x", "")
    sig = hmac.new(bytes.fromhex(secret_hex), unsigned, hashlib.sha256).digest()
    token = unsigned + b"." + base64.urlsafe_b64encode(sig).rstrip(b'=')
    token_str = token.decode()
    body = {"jsonrpc":"2.0","id":int(time.time()),"method":method,"params":params}
    r = requests.post(engine_url, json=body, headers={"Authorization": f"Bearer {token_str}"}, timeout=timeout)
    r.raise_for_status()
    j = r.json()
    if "error" in j: raise RuntimeError(f"Engine error: {j['error']}")
    return j["result"]

def _save_newpayload_host(exec_payload: dict, parent_hash: str, out_path: Path):
    obj = {"jsonrpc":"2.0","id":int(time.time()),"method":"engine_newPayloadV4","params":[exec_payload, [], parent_hash, []]}
    _atomic_write_json(out_path, obj)

def preparation_getpayload(engine_url: str, jwt_hex_path: Path, rpc_address: str, save_path: Path | None = None):
    ZERO32 = "0x" + ("00" * 32)
    txrlp_empty = None
    payload = _engine_with_jwt(engine_url, jwt_hex_path, "engine_getPayloadV4", [txrlp_empty, rpc_address])
    exec_payload = payload.get("executionPayload")
    parent_hash = exec_payload.get("parentHash") or ZERO32
    if save_path:
        _save_newpayload_host(exec_payload, parent_hash, save_path)
    _ = _engine_with_jwt(engine_url, jwt_hex_path, "engine_newPayloadV4", [exec_payload, [], parent_hash, []])
    block_hash = exec_payload.get("blockHash")
    fcs = {"headBlockHash": block_hash, "safeBlockHash": block_hash, "finalizedBlockHash": block_hash}
    _ = _engine_with_jwt(engine_url, jwt_hex_path, "engine_forkchoiceUpdatedV3", [fcs, None])
    return block_hash

def _cleanup():
    try:
        mp = CLEANUP.get("mitm")
        if mp and hasattr(mp, "poll") and mp.poll() is None:
            try: mp.terminate(); mp.wait(timeout=5)
            except Exception:
                try: mp.kill()
                except Exception: pass
    except Exception:
        pass
    if not CLEANUP.get("keep", False):
        try:
            if CLEANUP.get("container"):
                print_container_logs(CLEANUP["container"])
                stop_and_remove_container(CLEANUP["container"])
        except Exception:
            pass
        try:
            if CLEANUP.get("merged"):
                unmount_overlay(CLEANUP["merged"])
        except Exception:
            pass
        for key in ("upper","work","merged"):
            try:
                d = CLEANUP.get(key)
                if d: shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass

atexit.register(_cleanup)

def _sig_handler(signum, frame):
    try: print(f"[INFO] Caught signal {signum}; cleaning up...")
    except Exception: pass
    sys.exit(128 + signum)

for _s in (signal.SIGINT, signal.SIGTERM):
    try: signal.signal(_s, _sig_handler)
    except Exception: pass

def main():
    parser = argparse.ArgumentParser(description="EEST Stateful Generator")
    parser.add_argument("--chain", default="mainnet")
    parser.add_argument("--sim-parallelism", type=int, default=1)
    parser.add_argument("--test-path", default="tests")
    parser.add_argument("--fork", default="Prague")
    parser.add_argument("--rpc-endpoint", default=None)
    parser.add_argument("--seed-account-sweep-amount", default="1000 ether")
    parser.add_argument("--rpc-chain-id", type=int, default=None)
    parser.add_argument("--rpc-seed-key", required=True)
    parser.add_argument("--rpc-address", required=True)
    parser.add_argument("--no-snapshot", action="store_true")
    parser.add_argument("--refresh-snapshot", action="store_true")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    CLEANUP["keep"] = args.keep
    ensure_pip_pkg("requests")

    repo_dir = Path("execution-spec-tests")
    if not repo_dir.exists():
        run(["git", "clone", "https://github.com/ethereum/execution-spec-tests", str(repo_dir)])
    if not check_cmd_exists("uv"):
        run([sys.executable, "-m", "pip", "install", "-U", "uv"])
    run(["uv", "python", "install", "3.11"])
    run(["uv", "python", "pin", "3.11"], cwd=str(repo_dir))
    run(["uv", "sync", "--all-extras"], cwd=str(repo_dir))
    run(["uv", "pip", "install", "-e", "."], cwd=str(repo_dir))

    snapshot_dir = Path("execution-data")
    if not args.no_snapshot:
        if snapshot_dir.exists() and any(snapshot_dir.iterdir()) and not args.refresh_snapshot:
            pass
        else:
            if args.refresh_snapshot and snapshot_dir.exists():
                shutil.rmtree(snapshot_dir)
                snapshot_dir.mkdir(parents=True, exist_ok=True)
            download_snapshot(args.chain, snapshot_dir)
    else:
        snapshot_dir.mkdir(parents=True, exist_ok=True)

    jwt_path = ensure_jwt(Path("engine-jwt"))

    merged_dir = Path("overlay-merged")
    upper_dir  = Path("overlay-upper")
    work_dir   = Path("overlay-work")
    CLEANUP["merged"], CLEANUP["upper"], CLEANUP["work"] = merged_dir, upper_dir, work_dir

    stop_and_remove_container("eest-nethermind")
    ensure_overlay_mount(lower=snapshot_dir, upper=upper_dir, work=work_dir, merged=merged_dir)

    container_name = "eest-nethermind"
    CLEANUP["container"] = container_name
    _ = start_nethermind_container(
        chain=args.chain,
        db_dir=merged_dir,
        jwt_path=jwt_path,
        rpc_port=8545,
        engine_port=8551,
        name=container_name,
    )

    if not wait_for_port("127.0.0.1", 8545, timeout=180):
        print("ERROR: 8545 not reachable.")
        print_container_logs(container_name)
        stop_and_remove_container(container_name)
        try: unmount_overlay(merged_dir)
        except Exception: pass
        sys.exit(1)

    for _ in range(60):
        try:
            _ = rpc_call("http://127.0.0.1:8545", "eth_blockNumber")
            break
        except Exception:
            time.sleep(2)
    else:
        print("ERROR: JSON-RPC not responding.")
        print_container_logs(container_name)
        stop_and_remove_container(container_name)
        try: unmount_overlay(merged_dir)
        except Exception: pass
        sys.exit(1)

    chain_id = args.rpc_chain_id
    if chain_id is None:
        chain_id = CHAIN_TO_ID.get(args.chain.lower())
        if chain_id is None:
            try:
                cid_hex = rpc_call("http://127.0.0.1:8545", "eth_chainId")
                chain_id = int(cid_hex, 16)
            except Exception:
                chain_id = 1

    ensure_pip_pkg("mitmproxy")
    finalized_hash = ""

    try:
        for i in range(100):
            out_path = _next_indexed_path(Path("payloads"), "gas-bump")
            preparation_getpayload("http://127.0.0.1:8551", jwt_path, "EMPTY", save_path=out_path)
    except Exception as e:
        print(f"[WARN] Gas bump failed: {e}")

    try:
        funding_path = _next_indexed_path(Path("payloads"), "funding")
        finalized_hash = preparation_getpayload("http://127.0.0.1:8551", jwt_path, args.rpc_address, save_path=funding_path)
    except Exception as e:
        print(f"[WARN] Funding prep failed: {e}")

    mitm_config = {
        "rpc_direct": "http://127.0.0.1:8545",
        "engine_url": "http://127.0.0.1:8551",
        "jwt_hex_path": str(jwt_path),
        "finalized_block": finalized_hash,
    }
    Path("mitm_config.json").write_text(json.dumps(mitm_config), encoding="utf-8")

    addon_path = Path("mitm_addon.py")  # external file from above
    if not addon_path.exists():
        raise SystemExit("mitm_addon.py not found next to the script")

    mitm = start_mitm_proxy(addon_path, listen_port=8549, upstream="http://127.0.0.1:8545")
    CLEANUP["mitm"] = mitm

    try:
        if not wait_for_port("127.0.0.1", 8549, timeout=30):
            raise RuntimeError("mitmproxy failed to bind on 8549")

        tests_rpc = args.rpc_endpoint or "http://127.0.0.1:8549"
        uv_cmd = [
            "uv", "run", "execute", "remote", "-v",
            f"--fork={args.fork}",
            f"--rpc-seed-key={args.rpc_seed_key}",
            f"--rpc-chain-id={chain_id}",
            f"--rpc-endpoint={tests_rpc}",
            args.test_path,
            "--",
            "-m", "benchmark", "-n", "1",
        ]
        run(uv_cmd, cwd=str(repo_dir), check=True)
    finally:
        if not args.keep:
            try: print_container_logs(container_name)
            except Exception: pass
            stop_and_remove_container(container_name)
            try: unmount_overlay(merged_dir)
            except Exception: pass
            shutil.rmtree(upper_dir, ignore_errors=True)
            shutil.rmtree(work_dir, ignore_errors=True)
            shutil.rmtree(merged_dir, ignore_errors=True)
        print("Done.")

if __name__ == "__main__":
    main()
