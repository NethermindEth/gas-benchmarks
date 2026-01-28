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

def _parse_bool(value: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")

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
            time.sleep(0.2)
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
        for path in sorted(phase_dir.glob("**/*.txt")):
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


def _ensure_testing_placeholders(payload_dir: Path) -> None:
    testing_dir = payload_dir / "testing"

    indices: set[str] = set()
    numeric_indices: list[int] = []
    widths: list[int] = []
    if not testing_dir.is_dir():
        return
    for entry in testing_dir.iterdir():
        if not entry.is_dir():
            continue
        indices.add(entry.name)
        if entry.name.isdigit():
            numeric_indices.append(int(entry.name))
            widths.append(len(entry.name))

    if numeric_indices:
        min_idx = min(numeric_indices)
        max_idx = max(numeric_indices)
        pad_width = max(widths) if widths else 0
        for idx in range(min_idx, max_idx + 1):
            name = str(idx).zfill(pad_width) if pad_width else str(idx)
            indices.add(name)

    for idx in sorted(indices):
        target_dir = testing_dir / idx
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
        try:
            entries = list(target_dir.iterdir())
        except Exception:
            continue
        if any(entry.name != ".gitkeep" for entry in entries):
            continue
        gitkeep = target_dir / ".gitkeep"
        if gitkeep.exists():
            continue
        try:
            gitkeep.write_text("", encoding="utf-8")
        except Exception:
            pass


# --------------------------------------------------------------------------------

def generate_opcode_trace_json(testing_dir: Path, opcode_tracing_dir: Path, output_path: Path) -> None:
    """
    Process test files in testing_dir and create a JSON mapping test names to opcode counts.

    For each test file:
    1. Extract blockNumber from the engine_newPayloadV4 request (hex to int)
    2. Find corresponding opcode-trace-block-{blockNumber}.json
    3. Extract opcodeCounts from the trace file
    4. If multiple blocks, merge (sum) opcode counts
    """
    results = {}

    if not testing_dir.exists():
        print(f"[WARN] Testing directory {testing_dir} does not exist")
        return

    # Iterate through all numbered subdirectories
    for subdir in sorted(testing_dir.iterdir()):
        if not subdir.is_dir():
            continue

        # Find .txt files in the subdirectory
        for txt_file in subdir.glob("*.txt"):
            test_name = txt_file.stem

            try:
                lines = txt_file.read_text(encoding="utf-8").splitlines()
            except Exception as e:
                print(f"[WARN] Failed to read {txt_file}: {e}")
                continue

            # Collect all block numbers from the file
            block_numbers = []
            for line in lines:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    # Change this for other forks!
                    if data.get("method") == "engine_newPayloadV4":
                        params = data.get("params", [])
                        if params and isinstance(params[0], dict):
                            block_num_hex = params[0].get("blockNumber")
                            if block_num_hex:
                                block_numbers.append(int(block_num_hex, 16))
                except Exception:
                    continue

            if not block_numbers:
                print(f"[WARN] No blockNumber found in {txt_file}")
                continue

            # Merge opcode counts from all blocks
            merged_counts: dict[str, int] = {}
            for block_num in block_numbers:
                trace_file = opcode_tracing_dir / f"opcode-trace-block-{block_num}.json"
                if not trace_file.exists():
                    print(f"[WARN] Trace file not found: {trace_file}")
                    continue

                try:
                    trace_data = json.loads(trace_file.read_text(encoding="utf-8"))
                    opcode_counts = trace_data.get("opcodeCounts", {})
                    for opcode, count in opcode_counts.items():
                        merged_counts[opcode] = merged_counts.get(opcode, 0) + count
                except Exception as e:
                    print(f"[WARN] Failed to parse {trace_file}: {e}")
                    continue

            if merged_counts:
                results[test_name] = merged_counts

    # Write output JSON
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"[INFO] Opcode trace results written to {output_path}")


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
        time.sleep(0.05)
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
    getpayload_method = _getpayload_method_for_fork(args.fork)
    try:
        max_count = max(args.gas_bump_count, 1)
        last_log = time.monotonic()
        for idx in range(max_count):
            now = time.monotonic()
            if idx == 0 or idx == max_count - 1 or now - last_log >= 5:
                print(f"[DEBUG] Generating gas-bump payload {idx + 1}/{max_count}")
                last_log = now
            preparation_getpayload(
                "http://127.0.0.1:8551",
                jwt_path,
                "EMPTY",
                save_path=gas_bump_file,
                getpayload_method=getpayload_method,
            )
    except Exception as exc:
        print(f"[WARN] Gas bump failed: {exc}")
    finalized = ""
    try:
        finalized = preparation_getpayload(
            "http://127.0.0.1:8551",
            jwt_path,
            args.rpc_address,
            save_path=funding_file,
            getpayload_method=getpayload_method,
        )
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

def describe_mount(path: Path) -> str:
    try:
        mount_path = path.resolve()
        with open('/proc/mounts', 'r', encoding='utf-8') as mounts:
            for line in mounts:
                parts = line.split()
                if len(parts) >= 4 and parts[1] == str(mount_path):
                    return line.strip()
    except Exception:
        pass
    return ''

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

def start_nethermind_container(
    chain: str,
    db_dir: Path,
    jwt_path: Path,
    rpc_port=8545,
    engine_port=8551,
    name="eest-nethermind",
    image: str = "nethermindeth/nethermind:gp-hacked",
    genesis_path: Optional[Path] = None,
    trace_json: bool = False,
) -> str:
    subprocess.run(["docker", "pull", image], check=False)
    resolved_db = db_dir.resolve()
    resolved_jwt_parent = jwt_path.parent.resolve()
    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "-p",
        f"{rpc_port}:{rpc_port}",
        "-p",
        f"{engine_port}:{engine_port}",
        "-v",
        f"{str(resolved_db)}:/db",
        "-v",
        f"{str(resolved_jwt_parent)}:/jwt:ro",
    ]
    if trace_json:
        cmd += ["-v", f"{str(Path('opcode-tracing').resolve())}:/test-output"]
    genesis_volume = None
    if genesis_path:
        resolved_genesis = Path(genesis_path).resolve()
        if not resolved_genesis.is_file():
            raise FileNotFoundError(f"Genesis file not found at {resolved_genesis}")
        genesis_volume = ["-v", f"{str(resolved_genesis)}:/genesis/custom.json:ro"]
        cmd += genesis_volume

    cmd.append(image)

    if genesis_path:
        cmd += ["--config", "none", "--Init.ChainSpecPath", "/genesis/custom.json"]
    else:
        cmd += ["--config", str(chain)]

    cmd += [
        "--JsonRpc.Enabled",
        "true",
        "--JsonRpc.Host",
        "0.0.0.0",
        "--JsonRpc.Port",
        str(rpc_port),
        "--JsonRpc.EngineHost",
        "0.0.0.0",
        "--JsonRpc.EnginePort",
        str(engine_port),
        "--JsonRpc.JwtSecretFile",
        "/jwt/jwt.hex",
        "--JsonRpc.UnsecureDevNoRpcAuthentication",
        "true",
        "--JsonRpc.EnabledModules",
        "Eth,Net,Web3,Admin,Debug,Trace,TxPool",
        "--Blocks.TargetBlockGasLimit",
        "1000000000000",
        "--data-dir",
        "/db",
        "--log",
        "INFO",
        "--Network.MaxActivePeers",
        "0",
        "--TxPool.Size",
        "10000",
        "--TxPool.MaxTxSize",
        "null",
        "--Merge.TerminalTotalDifficulty",
        "0",
        "--Init.LogRules",
        "Consensus.Processing.ProcessingStats:Debug",
        "--Blocks.SingleBlockImprovementOfSlot",
        "10",
        "--Blocks.SecondsPerSlot",
        "2",
        "--Merge.NewPayloadBlockProcessingTimeout",
        "70000",
    ]
    if trace_json:
        cmd += [
            "--OpcodeTracing.Enabled", "true",
            "--OpcodeTracing.Mode", "Realtime",
            "--OpcodeTracing.StartBlock", "1",
            "--OpcodeTracing.EndBlock", "2",
            "--OpcodeTracing.OutputDirectory", "/test-output",
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

def _getpayload_method_for_fork(fork: str) -> str:
    fork_name = (fork or "").strip().lower()
    if fork_name == "osaka":
        return "engine_getPayloadV5"
    return "engine_getPayloadV4"


def preparation_getpayload(
    engine_url: str,
    jwt_hex_path: Path,
    rpc_address: str,
    save_path: Path | None = None,
    *,
    getpayload_method: str = "engine_getPayloadV4",
):
    """
    Build a payload on the engine (engine_getPayloadV4/V5), POST engine_newPayloadV4,
    then engine_forkchoiceUpdatedV3.
    If save_path is provided, append TWO minified JSON-RPC lines to that file:
      1) the engine_newPayloadV4 request body
      2) the engine_forkchoiceUpdatedV3 request body
    """
    ZERO32 = "0x" + ("00" * 32)
    txrlp_empty = None
    payload = _engine_with_jwt(engine_url, jwt_hex_path, getpayload_method, [txrlp_empty, rpc_address])
    exec_payload = payload.get("executionPayload")
    parent_hash = exec_payload.get("parentHash") or ZERO32

    # Send NP
    _ = _engine_with_jwt(engine_url, jwt_hex_path, "engine_newPayloadV4", [exec_payload, [], parent_hash, []])
    block_hash = exec_payload.get("blockHash")

    # Build FCU params (safe/finalized=head)
    fcs = {"headBlockHash": block_hash, "safeBlockHash": block_hash, "finalizedBlockHash": block_hash}
    # Send FCU
    _ = _engine_with_jwt(engine_url, jwt_hex_path, "engine_forkchoiceUpdatedV3", [fcs, None])

    if rpc_address and isinstance(rpc_address, str) and rpc_address.upper() != "EMPTY":
        try:
            balance_hex = rpc_call("http://127.0.0.1:8545", "eth_getBalance", [rpc_address, "latest"])
            if isinstance(balance_hex, str):
                balance_wei = int(balance_hex, 16)
                print(f"[INFO] Funding account {rpc_address} balance: {balance_wei} wei ({balance_hex})")
            else:
                print(f"[WARN] Unexpected eth_getBalance result for {rpc_address}: {balance_hex!r}")
        except Exception as exc:
            print(f"[WARN] Failed to read balance for {rpc_address}: {exc}")

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
        try:
            cleanup_dir = CLEANUP.get("data_dir_cleanup")
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
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
    parser.add_argument("--stubs-file", default=None, help="Path to address stubs JSON passed to execute remote")
    parser.add_argument("--snapshot-dir", default="execution-data", help="Path to snapshot DB (default: execution-data)")
    parser.add_argument("--no-snapshot", action="store_true")
    parser.add_argument("--refresh-snapshot", action="store_true")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Path to use for Nethermind execution data instead of overlay mounts.",
    )
    parser.add_argument(
        "--genesis-path",
        default=None,
        help="Path to a custom genesis JSON file to mount into Nethermind.",
    )
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
        "--fixed-opcode-count",
        "--fixed-ocpode-count",
        nargs="?",
        const="",
        default=None,
        help="Comma-separated fixed opcode counts to pass to execute remote instead of --gas-benchmark-values. "
             "Provide no value to pass an empty flag through.",
    )
    parser.add_argument(
        "--nethermind-image",
        default="nethermindeth/nethermind:gp-hacked",
        help="Docker image to use when launching the Nethermind container.",
    )
    parser.add_argument(
        "--gas-bump-count",
        type=int,
        default=301,
        help="Number of engine_getPayload iterations when generating gas-bump payload (default: 301).",
    )
    parser.add_argument(
        "--overlay-reorgs",
        type=_parse_bool,
        default=True,
        help="Enable per-scenario overlay + container restarts (true/false, default true).",
    )
    parser.add_argument(
        "--eest-repo",
        default="https://github.com/ethereum/execution-specs",
        help="Git repository URL for execution-specs (supports forks).",
    )
    parser.add_argument(
        "--eest-branch",
        default="main",
        help="Git branch of execution-specs to checkout before running (default: main).",
    )
    parser.add_argument(
        "--eest-no-pull",
        action="store_true",
        help="Skip fetching/pulling execution-specs when the repo already exists.",
    )
    parser.add_argument(
        "--parameter_filter",
        default="",
        help="Pass-through filter string forwarded to execute remote as -k \"...\".",
    )
    parser.add_argument(
        "--trace-json",
        action="store_true",
        help="Enable opcode tracing and generate JSON output mapping tests to opcode counts.",
    )
    parser.add_argument(
        "--trace-json-output",
        default="opcode_trace_results.json",
        help="Output path for the opcode trace results JSON (default: opcode_trace_results.json).",
    parser.add_argument(
        "--eest-mode",
        "--eest_mode",
        dest="eest_mode",
        default="repricing",
        help="Mode passed to execute remote via -m (supports repricings/benchmarks/stateful).",
    )
    parser.add_argument(
        "--eest-stateful-testing",
        action="store_true",
        help="Keep all testing payloads in the testing/ directory (no migration to setup).",
    )
    args = parser.parse_args()

    CLEANUP["keep"] = args.keep
    ensure_pip_pkg("requests")

    payloads_dir = _ensure_payloads_dir(Path(args.payload_dir))
    gas_value_source = args.fixed_opcode_count or args.gas_benchmark_values
    gas_values = [v.strip() for v in gas_value_source.split(",") if v.strip()]
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


    repo_dir = Path("execution-specs")
    repo_url = args.eest_repo
    if repo_dir.exists():
        if not args.eest_no_pull:
            run(["git", "remote", "set-url", "origin", repo_url], cwd=str(repo_dir))
            run(["git", "fetch", "origin"], cwd=str(repo_dir))
            run(["git", "checkout", args.eest_branch], cwd=str(repo_dir))
            run(["git", "pull", "origin", args.eest_branch], cwd=str(repo_dir))
        else:
            print("[INFO] --eest-no-pull specified; using existing execution-specs checkout as-is.")
    else:
        run([
            "git",
            "clone",
            "--branch",
            args.eest_branch,
            "--single-branch",
            repo_url,
            str(repo_dir),
        ])
    if not check_cmd_exists("uv"):
        run([sys.executable, "-m", "pip", "install", "-U", "uv"])
    run(["uv", "python", "install", "3.11"])
    run(["uv", "python", "pin", "3.11"], cwd=str(repo_dir))
    run(["uv", "sync", "--all-extras"], cwd=str(repo_dir))
    run(["uv", "pip", "install", "-e", ".", "--break-system-packages"], cwd=str(repo_dir))

    data_dir_path: Optional[Path] = None
    if args.data_dir:
        data_dir_path = Path(args.data_dir).expanduser().resolve()
        data_dir_path.mkdir(parents=True, exist_ok=True)

    genesis_file: Optional[Path] = None
    if args.genesis_path:
        candidate = Path(args.genesis_path).expanduser()
        resolved_candidate = candidate.resolve()
        if not resolved_candidate.is_file():
            raise SystemExit(f"Genesis file not found at {resolved_candidate}")
        genesis_file = resolved_candidate
        if data_dir_path is None:
            data_dir_path = Path("scripts/nethermind/execution-data").resolve()
            data_dir_path.mkdir(parents=True, exist_ok=True)

    use_overlay_base = data_dir_path is None and genesis_file is None
    base_data_dir = data_dir_path or Path("execution-data")

    if genesis_file is None:
        if not args.no_snapshot:
            if base_data_dir.exists() and any(base_data_dir.iterdir()) and not args.refresh_snapshot:
                pass
            else:
                if args.refresh_snapshot and base_data_dir.exists():
                    shutil.rmtree(base_data_dir)
                base_data_dir.mkdir(parents=True, exist_ok=True)
                download_snapshot(args.chain, base_data_dir)
        else:
            base_data_dir.mkdir(parents=True, exist_ok=True)
    else:
        if args.refresh_snapshot and base_data_dir.exists():
            shutil.rmtree(base_data_dir)
        base_data_dir.mkdir(parents=True, exist_ok=True)

    snapshot_dir = base_data_dir
    if not use_overlay_base:
        CLEANUP["data_dir_cleanup"] = snapshot_dir
    else:
        CLEANUP["data_dir_cleanup"] = None

    jwt_path = ensure_jwt(Path("engine-jwt"))

    if use_overlay_base:
        primary_merged = Path("overlay-merged")
        primary_upper = Path("overlay-upper")
        primary_work = Path("overlay-work")
        CLEANUP["primary_merged"], CLEANUP["primary_upper"], CLEANUP["primary_work"] = (
            primary_merged,
            primary_upper,
            primary_work,
        )
    else:
        primary_merged = snapshot_dir.resolve()
        primary_upper = primary_work = None
        CLEANUP["primary_merged"] = CLEANUP["primary_upper"] = CLEANUP["primary_work"] = None

    overlay_reorgs_enabled = args.overlay_reorgs and use_overlay_base
    if args.overlay_reorgs and not use_overlay_base:
        print("[INFO] Overlay reorgs disabled because overlay filesystem is not in use.")

    if overlay_reorgs_enabled:
        scenario_merged = Path("overlay-scenario-merged")
        scenario_upper = Path("overlay-scenario-upper")
        scenario_work = Path("overlay-scenario-work")
        CLEANUP["scenario_merged"], CLEANUP["scenario_upper"], CLEANUP["scenario_work"] = (
            scenario_merged,
            scenario_upper,
            scenario_work,
        )
    else:
        scenario_merged = scenario_upper = scenario_work = None
        CLEANUP["scenario_merged"] = CLEANUP["scenario_upper"] = CLEANUP["scenario_work"] = None

    if args.trace_json:
        Path("opcode-tracing").mkdir(parents=True, exist_ok=True)

    stop_and_remove_container("eest-nethermind")
    if use_overlay_base:
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
            genesis_path=genesis_file,
            trace_json=args.trace_json,
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
                time.sleep(0.5)
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
        if not overlay_reorgs_enabled:
            return
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
        "fork": args.fork,
        "eest_stateful_testing": args.eest_stateful_testing,
        "finalized_block": finalized_hash or "",
        "payload_dir": str(payloads_dir),
        "reuse_globals": reuse_globals,
        "mitm_log_path": str(Path("mitm.log").resolve()),
        "merged_log_path": str(Path("mitm_nethermind.log").resolve()),
        "nethermind_container": container_name,
        "light_logs": True,
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
        mode_key = (args.eest_mode or "").strip().lower()
        mode_map = {
            "repricing": "repricing",
            "repricings": "repricing",
            "benchmark": "benchmarks",
            "benchmarks": "benchmarks",
            "stateful": "stateful",
        }
        if mode_key not in mode_map:
            raise SystemExit(f"Unsupported --eest-mode value: {args.eest_mode!r}")
        eest_mode = mode_map[mode_key]
        uv_cmd = [
            "uv", "run", "execute", "remote", "-v",
            f"--fork={args.fork}",
            f"--rpc-seed-key={args.rpc_seed_key}",
            f"--rpc-chain-id={chain_id}",
            f"--rpc-endpoint={tests_rpc}",
        ]
        if args.fixed_opcode_count is not None:
            if args.fixed_opcode_count == "":
                uv_cmd.append("--fixed-opcode-count=")
            else:
                uv_cmd.append(f"--fixed-opcode-count={args.fixed_opcode_count}")
        elif args.gas_benchmark_values:
            uv_cmd.append(f"--gas-benchmark-values={args.gas_benchmark_values}")
        uv_cmd += [
            #"--eoa-fund-amount-default", "3100000000000000000",
            "--tx-wait-timeout", "30",
            "--eoa-start", "103835740027347086785932208981225044632444623980288738833340492242305523519088",
            "--skip-cleanup",
            args.test_path,
            "--",
            "-m", eest_mode, "-n", "1",
        ]
        if args.parameter_filter:
            uv_cmd.extend(["-k", args.parameter_filter])
        stubs_source = args.stubs_file or os.environ.get("EEST_ADDRESS_STUBS")
        if stubs_source:
            stubs_path = Path(stubs_source).expanduser()
            if stubs_path.exists():
                uv_cmd.extend(["--address-stubs", str(stubs_path.resolve())])
            else:
                print(f"[WARN] Address stubs file {stubs_path} not found; ignoring.")

        run_env = os.environ.copy()
        src_path = str((repo_dir / "src").resolve())
        existing_path = run_env.get("PYTHONPATH", "")
        if existing_path:
            run_env["PYTHONPATH"] = os.pathsep.join([src_path, existing_path])
        else:
            run_env["PYTHONPATH"] = src_path
        run_env["EEST_POLL_INTERVAL"] = "0.01"

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

            if not overlay_reorgs_enabled:
                print("[INFO] Overlay reorgs disabled; skipping scenario overlay and node restart.")
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
                return

            try:
                stop_and_remove_container(container_name)
            except Exception:
                pass
            try:
                prepare_scenario_overlay()
                mount_line = describe_mount(scenario_merged)
                if mount_line:
                    print(f"[DEBUG] Scenario overlay mount: {mount_line}")
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
                time.sleep(0.1)
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
        if args.trace_json:
            generate_opcode_trace_json(
                testing_dir=Path(payloads_dir / "testing"),
                opcode_tracing_dir=Path("opcode-tracing"),
                output_path=Path(args.trace_json_output),
            )
        if return_code != 0:
            raise subprocess.CalledProcessError(return_code, uv_cmd)
        if len(gas_values) == 1:
            _append_suffix_to_scenarios(payloads_dir, gas_values[0])
        _ensure_testing_placeholders(payloads_dir)
    finally:
        if not args.keep:
            try:
                print_container_logs(container_name)
            except Exception:
                pass
            stop_and_remove_container(container_name)
            if overlay_reorgs_enabled and scenario_merged is not None:
                try:
                    unmount_overlay(scenario_merged)
                except Exception:
                    pass
            if use_overlay_base:
                try:
                    unmount_overlay(primary_merged)
                except Exception:
                    pass
            cleanup_paths = []
            if use_overlay_base:
                cleanup_paths.extend([primary_upper, primary_work, primary_merged])
                if overlay_reorgs_enabled:
                    cleanup_paths.extend([scenario_upper, scenario_work, scenario_merged])
            for path in cleanup_paths:
                if path:
                    shutil.rmtree(path, ignore_errors=True)
            if not use_overlay_base and snapshot_dir:
                shutil.rmtree(snapshot_dir, ignore_errors=True)
        print("Done.")

if __name__ == "__main__":
    main()
