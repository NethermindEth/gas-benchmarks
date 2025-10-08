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
from typing import Optional
import atexit
import re

CHAIN_TO_ID = {
    "mainnet": 1,
    "ethereum": 1,
    "sepolia": 11155111,
    "holesky": 17000,
    "goerli": 5,
}

CLEANUP = {
    "keep": False,
    "container": None,
    "primary_merged": None,
    "primary_upper": None,
    "primary_work": None,
    "scenario_merged": None,
    "scenario_upper": None,
    "scenario_work": None,
    "mitm": None,
}

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

# ---------------------- new helpers for line-based outputs ----------------------

def _ensure_payloads_dir(base_path: Path) -> Path:
    base = base_path.expanduser()
    base.mkdir(parents=True, exist_ok=True)
    return base.resolve()

def _minified_json_line(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"))

def _append_line(path: Path, line: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def _truncate_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8"):
        pass

def _safe_suffix(value: str) -> str:
    cleaned = "".join(ch for ch in value if ch.isalnum() or ch in ("-", "_"))
    return cleaned or value.strip()


def _append_suffix_to_scenarios(payload_dir: Path, suffix: str) -> None:
    suffix = _safe_suffix(suffix)
    if not suffix:
        return
    if suffix.upper().endswith("M"):
        suffix = suffix.upper()
    elif suffix.isdigit():
        suffix = f"{suffix}M"

    for phase in ("setup", "testing", "cleanup"):
        phase_dir = payload_dir / phase
        if not phase_dir.is_dir():
            continue
        for path in sorted(phase_dir.glob("*.txt")):
            stem = path.stem
            if stem.endswith(f"_{suffix}") or "gas-value" in stem:
                continue
            base_name = stem
            target = path.with_name(f"{base_name}_{suffix}{path.suffix}")
            counter = 1
            while target.exists():
                target = path.with_name(f"{base_name}_{suffix}_{counter}{path.suffix}")
                counter += 1
            try:
                path.rename(target)
            except Exception:
                pass

# --------------------------------------------------------------------------------

def _read_json_file(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_resume_signal(path: Path, token: str, scenario: str) -> None:
    payload = {
        "token": token,
        "scenario": scenario,
        "timestamp": time.time(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _wait_for_resume_consumed(path: Path, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while path.exists():
        if time.time() > deadline:
            return False
        time.sleep(0.2)
    return True

def _block_exists(rpc_url: str, block_hash: str) -> bool:
    if not block_hash:
        return False
    try:
        result = rpc_call(rpc_url, "eth_getBlockByHash", [block_hash, False])
    except Exception:
        return False
    return bool(result)


def _generate_preparation_payloads(jwt_path: Path, args, gas_bump_file: Path, funding_file: Path) -> str:
    print("[INFO] Regenerating gas-bump and funding payloads.")
    _truncate_file(gas_bump_file)
    _truncate_file(funding_file)
    try:
        for _ in range(301):
            preparation_getpayload("http://127.0.0.1:8551", jwt_path, "EMPTY", save_path=gas_bump_file)
    except Exception as exc:
        print(f"[WARN] Gas bump failed: {exc}")
    finalized = ""
    try:
        finalized = preparation_getpayload("http://127.0.0.1:8551", jwt_path, args.rpc_address, save_path=funding_file)
    except Exception as exc:
        print(f"[WARN] Funding prep failed: {exc}")
    return finalized or ""

def _latest_block_hash_from_payload_file(path: Path) -> Optional[str]:
    try:
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except Exception:
        return None
    for line in reversed(lines):
        try:
            obj = json.loads(line)
        except Exception:
            continue
        method = obj.get("method")
        if not isinstance(method, str):
            continue
        if method.startswith("engine_newPayload"):
            params = obj.get("params") or []
            if params and isinstance(params[0], dict):
                block_hash = params[0].get("blockHash")
                if isinstance(block_hash, str):
                    return block_hash
    return None

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
                               rpc_port=8545, engine_port=8551, name="eest-nethermind",
                               image: str = "nethermindeth/nethermind:gp-hacked") -> str:
    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "-p", f"{rpc_port}:{rpc_port}",
        "-p", f"{engine_port}:{engine_port}",
        "-v", f"{str(db_dir.resolve())}:/db",
        "-v", f"{str(jwt_path.parent.resolve())}:/jwt:ro",
        image,
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
        "--log", "INFO",
        "--Network.MaxActivePeers", "0",
        "--TxPool.Size", "10000",
        "--TxPool.MaxTxSize", "null",
        "--Init.LogRules", "Consensus.Processing.ProcessingStats:Debug",
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
        Path("nethermind.log").write_text(out.stdout, encoding="utf-8")
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

# ----------------- changed: append NP and FCU to a single .txt file -----------------

def preparation_getpayload(engine_url: str, jwt_hex_path: Path, rpc_address: str, save_path: Path | None = None):
    """
    Build a payload on the engine, POST engine_newPayloadV4, then engine_forkchoiceUpdatedV3.
    If save_path is provided, append TWO minified JSON-RPC lines to that file:
      1) the engine_newPayloadV4 request body
      2) the engine_forkchoiceUpdatedV3 request body
    """
    ZERO32 = "0x" + ("00" * 32)
    txrlp_empty = None
    payload = _engine_with_jwt(engine_url, jwt_hex_path, "engine_getPayloadV4", [txrlp_empty, rpc_address])
    exec_payload = payload.get("executionPayload")
    parent_hash = exec_payload.get("parentHash") or ZERO32

    # Send NP
    _ = _engine_with_jwt(engine_url, jwt_hex_path, "engine_newPayloadV4", [exec_payload, [], parent_hash, []])
    block_hash = exec_payload.get("blockHash")

    # Build FCU params (safe/finalized=head)
    fcs = {"headBlockHash": block_hash, "safeBlockHash": block_hash, "finalizedBlockHash": block_hash}
    # Send FCU
    _ = _engine_with_jwt(engine_url, jwt_hex_path, "engine_forkchoiceUpdatedV3", [fcs, None])

    # Append NP + FCU requests as lines if requested
    if save_path is not None:
        np_body = {"jsonrpc":"2.0","id":int(time.time()),"method":"engine_newPayloadV4","params":[exec_payload, [], parent_hash, []]}
        fcu_body = {"jsonrpc":"2.0","id":int(time.time()),"method":"engine_forkchoiceUpdatedV3","params":[fcs, None]}
        _append_line(save_path, _minified_json_line(np_body))
        _append_line(save_path, _minified_json_line(fcu_body))

    return block_hash

# -----------------------------------------------------------------------------------

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
            if CLEANUP.get("scenario_merged"):
                unmount_overlay(CLEANUP["scenario_merged"])
        except Exception:
            pass
        try:
            if CLEANUP.get("primary_merged"):
                unmount_overlay(CLEANUP["primary_merged"])
        except Exception:
            pass
        for key in (
            "scenario_upper","scenario_work","scenario_merged",
            "primary_upper","primary_work","primary_merged"
        ):
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
    parser.add_argument(
        "--payload-dir",
        default="eest_stateful",
        help="Directory where generated stateful payloads are written.",
    )
    parser.add_argument(
        "--gas-benchmark-values",
        default="30,60,90,120,150",
        help="Comma-separated gas benchmark values to pass to execute remote.",
    )
    parser.add_argument(
        "--nethermind-image",
        default="nethermindeth/nethermind:gp-hacked",
        help="Docker image to use when launching the Nethermind container.",
    )
    args = parser.parse_args()

    CLEANUP["keep"] = args.keep
    ensure_pip_pkg("requests")

    payloads_dir = _ensure_payloads_dir(Path(args.payload_dir))
    gas_values = [v.strip() for v in args.gas_benchmark_values.split(",") if v.strip()]
    scenario_order_file = payloads_dir / "scenario_order.json"
    if scenario_order_file.exists():
        scenario_order_file.unlink()
    gas_bump_file = payloads_dir / "gas-bump.txt"
    funding_file  = payloads_dir / "funding.txt"
    setup_global_file = payloads_dir / "setup-global-test.txt"
    reuse_preparation = gas_bump_file.exists() and funding_file.exists()
    reuse_globals = setup_global_file.exists()


    for subdir in ("setup", "testing", "cleanup"):
        sub_path = payloads_dir / subdir
        if sub_path.exists():
            shutil.rmtree(sub_path)

    control_dir = payloads_dir / "_control"
    if control_dir.exists():
        shutil.rmtree(control_dir)
    control_dir.mkdir(parents=True, exist_ok=True)
    pause_file = control_dir / "pause.json"
    resume_file = control_dir / "resume.json"


    repo_dir = Path("execution-spec-tests")
    if not repo_dir.exists():
        run(["git", "clone", "https://github.com/ethereum/execution-spec-tests", str(repo_dir)])
    if not check_cmd_exists("uv"):
        run([sys.executable, "-m", "pip", "install", "-U", "uv"])
    run(["uv", "python", "install", "3.11"])
    run(["uv", "python", "pin", "3.11"], cwd=str(repo_dir))
    run(["uv", "sync", "--all-extras"], cwd=str(repo_dir))
    run(["uv", "pip", "install", "-e", ".", "--break-system-packages"], cwd=str(repo_dir))

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

    primary_merged = Path("overlay-merged")
    primary_upper  = Path("overlay-upper")
    primary_work   = Path("overlay-work")
    CLEANUP["primary_merged"], CLEANUP["primary_upper"], CLEANUP["primary_work"] = primary_merged, primary_upper, primary_work

    scenario_merged = Path("overlay-scenario-merged")
    scenario_upper  = Path("overlay-scenario-upper")
    scenario_work   = Path("overlay-scenario-work")
    CLEANUP["scenario_merged"], CLEANUP["scenario_upper"], CLEANUP["scenario_work"] = scenario_merged, scenario_upper, scenario_work

    stop_and_remove_container("eest-nethermind")
    ensure_overlay_mount(lower=snapshot_dir, upper=primary_upper, work=primary_work, merged=primary_merged)

    container_name = "eest-nethermind"
    CLEANUP["container"] = container_name
    active_db_dir = primary_merged

    def restart_node(db_dir: Path, *, show_logs: bool = False) -> None:
        nonlocal active_db_dir
        stop_and_remove_container(container_name)
        _ = start_nethermind_container(
            chain=args.chain,
            db_dir=db_dir,
            jwt_path=jwt_path,
            rpc_port=8545,
            engine_port=8551,
            name=container_name,
            image=args.nethermind_image,
        )
        if not wait_for_port("127.0.0.1", 8545, timeout=180):
            print("ERROR: 8545 not reachable.")
            print_container_logs(container_name)
            stop_and_remove_container(container_name)
            raise RuntimeError("JSON-RPC port not reachable")
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
            raise RuntimeError("JSON-RPC not responding")
        if show_logs:
            print_container_logs(container_name)
        active_db_dir = db_dir

    try:
        restart_node(primary_merged, show_logs=True)
    except RuntimeError:
        try: unmount_overlay(primary_merged)
        except Exception: pass
        sys.exit(1)

    scenario_overlay_ready = False

    def prepare_scenario_overlay() -> None:
        nonlocal scenario_overlay_ready
        if scenario_overlay_ready:
            try: unmount_overlay(scenario_merged)
            except Exception:
                pass
        shutil.rmtree(scenario_upper, ignore_errors=True)
        shutil.rmtree(scenario_work, ignore_errors=True)
        shutil.rmtree(scenario_merged, ignore_errors=True)
        ensure_overlay_mount(lower=primary_merged, upper=scenario_upper, work=scenario_work, merged=scenario_merged)
        scenario_overlay_ready = True

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

    try:
        prepare_scenario_overlay()
    except Exception as exc:
        print(f"[WARN] Failed to prepare scenario overlay before tests: {exc}")
    else:
        try:
            restart_node(scenario_merged, show_logs=False)
        except RuntimeError as exc:
            print(f"[ERROR] Unable to restart node on scenario overlay before tests: {exc}")
            try: unmount_overlay(scenario_merged)
            except Exception: pass
            sys.exit(1)

    finalized_hash = ""
    rpc_url = "http://127.0.0.1:8545"
    if reuse_preparation:
        print("[INFO] Reusing existing gas-bump and funding payloads.")
        finalized_hash = _latest_block_hash_from_payload_file(funding_file) or ""
        if not finalized_hash or not _block_exists(rpc_url, finalized_hash):
            print("[WARN] Reused funding payload is missing finalized block; regenerating preparations.")
            reuse_preparation = False

    if not reuse_preparation:
        finalized_hash = _generate_preparation_payloads(jwt_path, args, gas_bump_file, funding_file)

    if finalized_hash and not _block_exists(rpc_url, finalized_hash):
        print(f"[WARN] Finalized block {finalized_hash} not found; clearing anchor.")
        finalized_hash = ""

    mitm_config = {
        "rpc_direct": "http://127.0.0.1:8545",
        "engine_url": "http://127.0.0.1:8551",
        "jwt_hex_path": str(jwt_path),
        "finalized_block": finalized_hash or "",
        "payload_dir": str(payloads_dir),
        "reuse_globals": reuse_globals,
        "skip_cleanup": True,
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
            f"--gas-benchmark-values={args.gas_benchmark_values}",
            "--eoa-start", "103835740027347086785932208981225044632444623980288738833340492242305523519088",
            "--skip-cleanup",
            args.test_path,
            "--",
            "-m", "benchmark", "-n", "1",
        ]

        run_env = os.environ.copy()
        src_path = str((repo_dir / "src").resolve())
        existing_path = run_env.get("PYTHONPATH", "")
        if existing_path:
            run_env["PYTHONPATH"] = os.pathsep.join([src_path, existing_path])
        else:
            run_env["PYTHONPATH"] = src_path

        tests_proc = subprocess.Popen(uv_cmd, cwd=str(repo_dir), env=run_env)
        processed_tokens: set[str] = set()
        return_code: Optional[int] = None

        def handle_pause(payload: dict) -> None:
            token = str(payload.get("token") or "")
            if not token:
                print(f"[WARN] Ignoring pause payload without token: {payload}")
                return
            scenario_name = payload.get("scenario") or "unknown"
            stage = payload.get("stage")
            block_hash = payload.get("blockHash")
            print(f"[STATE] Pause requested: scenario={scenario_name} stage={stage} token={token} block={block_hash}")
            try:
                stop_and_remove_container(container_name)
            except Exception:
                pass
            try:
                prepare_scenario_overlay()
            except Exception as exc:
                print(f"[ERROR] Unable to prepare scenario overlay: {exc}")
                raise
            try:
                restart_node(scenario_merged, show_logs=False)
            except RuntimeError as exc:
                print(f"[ERROR] Failed to restart node for scenario {scenario_name}: {exc}")
                raise
            try:
                resume_file.unlink(missing_ok=True)
            except Exception:
                pass
            _write_resume_signal(resume_file, token, scenario_name)
            if not _wait_for_resume_consumed(resume_file, timeout=300.0):
                print(f"[WARN] Resume signal not consumed for scenario {scenario_name} (token={token}) within timeout")
            else:
                print(f"[STATE] Resume acknowledged for scenario {scenario_name} token={token}")
            processed_tokens.add(token)

        try:
            while True:
                payload = _read_json_file(pause_file) if pause_file.exists() else None
                if payload:
                    token = str(payload.get("token") or "")
                    if token and token not in processed_tokens:
                        handle_pause(payload)
                        continue
                ret = tests_proc.poll()
                if ret is not None:
                    payload = _read_json_file(pause_file) if pause_file.exists() else None
                    if payload:
                        token = str(payload.get("token") or "")
                        if token and token not in processed_tokens:
                            handle_pause(payload)
                    break
                time.sleep(0.5)
            return_code = tests_proc.wait()
        finally:
            if tests_proc.poll() is None:
                tests_proc.terminate()
                try:
                    tests_proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    tests_proc.kill()
                    tests_proc.wait()

        if return_code is None:
            return_code = tests_proc.returncode
        if return_code != 0:
            raise subprocess.CalledProcessError(return_code, uv_cmd)
        if len(gas_values) == 1:
            _append_suffix_to_scenarios(payloads_dir, gas_values[0])
    finally:
        if not args.keep:
            try:
                print_container_logs(container_name)
            except Exception:
                pass
            stop_and_remove_container(container_name)
            try:
                unmount_overlay(scenario_merged)
            except Exception:
                pass
            try:
                unmount_overlay(primary_merged)
            except Exception:
                pass
            for path in (
                scenario_upper, scenario_work, scenario_merged,
                primary_upper, primary_work, primary_merged,
            ):
                shutil.rmtree(path, ignore_errors=True)
        print("Done.")

if __name__ == "__main__":
    main()
