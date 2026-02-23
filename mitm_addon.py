from __future__ import annotations

import base64
import hmac
import hashlib
import json
import os
import pathlib
import re
import shutil
import threading
import subprocess
import time
import uuid
from datetime import datetime
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from mitmproxy import http, ctx

# ---------------------------------------------------------------------------
# Configuration & Globals
# ---------------------------------------------------------------------------
_CFG_PATH = os.environ.get("MITM_ADDON_CONFIG", "mitm_config.json")
with open(_CFG_PATH, "r", encoding="utf-8") as _f:
    _CFG = json.load(_f)

RPC_DIRECT: str = _CFG["rpc_direct"]
ENGINE_URL: str = _CFG["engine_url"]
JWT_HEX_PATH: str = _CFG["jwt_hex_path"]
FINALIZED_BLOCK: str = _CFG.get("finalized_block") or ""
HOOK_BLOCK: str = _CFG.get("hook_block") or ""
SKIP_CLEANUP: bool = bool(_CFG.get("skip_cleanup"))
DISABLE_OVERLAY_RESTORE: bool = bool(_CFG.get("disable_overlay_restore"))
OVERLAY_RESTORE_TRIGGER_ADDRESS: str = _CFG.get("overlay_restore_trigger_address", "0x86cf016fb873d50a7b8f31eb154c9234dd31b058").lower()
REUSE_GLOBALS: bool = bool(_CFG.get("reuse_globals"))
FORK: str = str(_CFG.get("fork") or "Prague")

def _cfg_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return default

EEST_STATEFUL_TESTING: bool = _cfg_bool(_CFG.get("eest_stateful_testing"), False)
_TESTING_BUILDBLOCK_TIMESTAMP_HACK: bool = _cfg_bool(_CFG.get("testing_buildblock_timestamp_hack"), False)


def _newpayload_method_for_fork(fork: str) -> str:
    return "engine_newPayloadV4"


_NEWPAYLOAD_METHOD = _newpayload_method_for_fork(FORK)

_DYN_FINALIZED: str = FINALIZED_BLOCK

_u = urlparse(ENGINE_URL)
_ENGINE_HOST = _u.hostname or "127.0.0.1"
_ENGINE_PORT = _u.port or (443 if (_u.scheme == "https") else 80)
_ENGINE_PATH = _u.path or "/"

_LOG_FILE_RAW = _CFG.get("mitm_log_path") or _CFG.get("log_file")
_LOG_FILE_PATH = pathlib.Path(_LOG_FILE_RAW).expanduser() if _LOG_FILE_RAW else pathlib.Path("/root/mitm_logs.log")
if not _LOG_FILE_PATH.is_absolute():
    _LOG_FILE_PATH = _LOG_FILE_PATH.resolve()
_LOG_FILE = str(_LOG_FILE_PATH)

_FULL_LOG_RAW = _CFG.get("mitm_full_log_path") or _CFG.get("full_log_path")
_FULL_LOG_PATH = pathlib.Path(_FULL_LOG_RAW).expanduser() if _FULL_LOG_RAW else _LOG_FILE_PATH.with_name("mitm_full.log")
if not _FULL_LOG_PATH.is_absolute():
    _FULL_LOG_PATH = _FULL_LOG_PATH.resolve()
if "mitm_full_log" in _CFG:
    _FULL_LOG_ENABLED = _cfg_bool(_CFG.get("mitm_full_log"), False)
elif "full_log" in _CFG:
    _FULL_LOG_ENABLED = _cfg_bool(_CFG.get("full_log"), False)
else:
    _FULL_LOG_ENABLED = bool(_FULL_LOG_RAW)

_MERGED_LOG_RAW = _CFG.get("merged_log_path")
_MERGED_LOG_PATH = pathlib.Path(_MERGED_LOG_RAW).expanduser() if _MERGED_LOG_RAW else _LOG_FILE_PATH.with_name("mitm_nethermind.log")
if not _MERGED_LOG_PATH.is_absolute():
    _MERGED_LOG_PATH = _MERGED_LOG_PATH.resolve()

_NETHERMIND_CONTAINER = _CFG.get("nethermind_container") or "eest-nethermind"
_LIGHT_LOG = bool(_CFG.get("light_logs", True))
_MITM_QUIET = bool(_CFG.get("mitm_quiet", True))
_MITM_TERMLOG_VERBOSITY = _CFG.get("mitm_termlog_verbosity", "error")
_MITM_EVENTLOG_VERBOSITY = _CFG.get("mitm_eventlog_verbosity", "error")
_MITM_FLOWLIST_VERBOSITY = _CFG.get("mitm_flowlist_verbosity", "error")
_MITM_FLOW_DETAIL = _CFG.get("mitm_flow_detail", 0)
try:
    _MITM_FLOW_DETAIL = int(_MITM_FLOW_DETAIL)
except Exception:
    _MITM_FLOW_DETAIL = 0
_LIGHT_PREFIX_KEEP = ("[MITM]", "[NM]", "[SENDRAW]", "ERROR", "WARN", "overlay", "PAUSE", "RESUME")
_NM_LAST_TS: Optional[str] = None

# Synchronization / state
_GROUP_LOCK = threading.Lock()
_ACTIVE_GRP: Optional[Tuple[str, str, str]] = None  # (file_base, test_name, phase)
_LAST_SENDRAW_TS: float = 0.0  # timestamp of the last buffered sendRawTransaction (diagnostic only)
_PENDING: bool = False
_STAGE: Dict[Tuple[str, str, str], int] = {}
_BUF: List[Tuple[str, Any, Optional[str], Optional[int]]] = []  # list of (txrlp_hex, original_id, extra_data_label, tx_index)
_STOP: bool = False
_LIFECYCLE_TS: Optional[int] = None

# Per-scenario bookkeeping
_SEEN_SCENARIOS: set[str] = set()
_TESTING_SEEN_COUNT: Dict[str, int] = {}

# Scenario ordering (stable numbering for later replay)
_SCENARIO_INDEX: Dict[str, int] = {}
_SCENARIO_SEQUENCE: List[Dict[str, Any]] = []

def _write_scenario_order() -> None:
    if _SCENARIO_ORDER_FILE is None:
        return
    try:
        _SCENARIO_ORDER_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SCENARIO_ORDER_FILE.write_text(json.dumps(_SCENARIO_SEQUENCE, indent=2), encoding="utf-8")
    except Exception as e:
        _log(f"scenario order write failed: {e}")


def _register_scenario(name: str) -> int:
    idx = _SCENARIO_INDEX.get(name)
    if idx is not None:
        return idx
    idx = len(_SCENARIO_INDEX) + 1
    _SCENARIO_INDEX[name] = idx
    _SCENARIO_SEQUENCE.append({"index": idx, "name": name})
    _write_scenario_order()
    return idx


# --- Test lifecycle + global no-phase bookkeeping ---
_TESTS_STARTED: bool = False  # flipped True on first phased test sendraw (setup/testing/cleanup)

# Base paths
# Allow overriding payload directory via config (defaults to repo-relative path)
_PAYLOADS_DIR = pathlib.Path(_CFG.get("payload_dir", "eest_stateful")).expanduser()
if not _PAYLOADS_DIR.is_absolute():
    _PAYLOADS_DIR = _PAYLOADS_DIR.resolve()
_SETUP_DIR = _PAYLOADS_DIR / "setup"
_TESTING_DIR = _PAYLOADS_DIR / "testing"
_CLEANUP_DIR = _PAYLOADS_DIR / "cleanup"

_PHASE_BASE_DIRS: Dict[str, pathlib.Path] = {
    "setup": _SETUP_DIR,
    "testing": _TESTING_DIR,
    "cleanup": _CLEANUP_DIR,
}

_CONTROL_DIR = _PAYLOADS_DIR / "_control"
_PAUSE_FILE = _CONTROL_DIR / "pause.json"
_RESUME_FILE = _CONTROL_DIR / "resume.json"
_PAUSE_LOCK = threading.Lock()
_PAUSE_EVENT = threading.Event()
_PAUSE_EVENT.set()
_PAUSE_TOKEN: Optional[str] = None
_PAUSE_SCENARIO: Optional[str] = None
_CONTROL_THREAD: Optional[threading.Thread] = None
_PENDING_OVERLAY: Optional[Tuple[str, int, Optional[str]]] = None  # (scenario, stage, block)
_PENDING_TX_HASHES: set = set()          # tx hashes awaiting eth_getTransactionByHash confirmation
_PENDING_TX_LOCK = threading.Lock()
_ALL_TX_CONFIRMED_EVENT = threading.Event()
_ALL_TX_CONFIRMED_EVENT.set()            # initially set (no pending txs)
_SEPARATOR_READY_FOR_NEXT_SETUP: bool = False
_PENDING_SEPARATOR_PAIR: Optional[Tuple[Dict[str, Any], Dict[str, Any]]] = None
_LEGACY_PHASE_DIRS_CLEANED: bool = False

_OVERLAY_PRIMED: bool = False


def _scenario_file_path(phase: str, scenario: str) -> pathlib.Path:
    base = _PHASE_BASE_DIRS.get(phase.lower())
    if base is None:
        raise ValueError(f"unknown phase '{phase}'")
    _register_scenario(scenario)
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{scenario}.txt"


_SCENARIO_ORDER_FILE_RAW = _CFG.get("scenario_order_file")
if isinstance(_SCENARIO_ORDER_FILE_RAW, str) and _SCENARIO_ORDER_FILE_RAW.strip():
    _SCENARIO_ORDER_FILE = pathlib.Path(_SCENARIO_ORDER_FILE_RAW).expanduser()
    if not _SCENARIO_ORDER_FILE.is_absolute():
        _SCENARIO_ORDER_FILE = (_PAYLOADS_DIR / _SCENARIO_ORDER_FILE).resolve()
    else:
        _SCENARIO_ORDER_FILE = _SCENARIO_ORDER_FILE.resolve()
elif _SCENARIO_ORDER_FILE_RAW:
    _SCENARIO_ORDER_FILE = pathlib.Path(_SCENARIO_ORDER_FILE_RAW).expanduser().resolve()
else:
    _SCENARIO_ORDER_FILE = None

# Legacy/unknown helpers
_GLOBAL_SETUP_FILE = _PAYLOADS_DIR / "global-setup.txt"   # only used for one-time migration on load
_UNKNOWN_FILE = _PAYLOADS_DIR / "unknown.txt"

# Global no-phase lifecycle files (root of payloads/)
_SETUP_GLOBAL_FILE = _PAYLOADS_DIR / "setup-global-test.txt"
_MIDDLE_GLOBAL_FILE = _PAYLOADS_DIR / "middle-global-tests.txt"
_CURRENT_LAST_FILE = _PAYLOADS_DIR / "current-last-global-test.txt"

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _append_lines(path: pathlib.Path, lines: List[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")
    except Exception:
        pass


def _now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _log(msg: str, *, to_merged: bool = False) -> None:
    try:
        line = f"{_now_ts()} {msg}"
        if _FULL_LOG_ENABLED:
            _append_lines(_FULL_LOG_PATH, [line])
        if _LIGHT_LOG and not to_merged:
            if not msg.startswith(_LIGHT_PREFIX_KEEP):
                return
        _append_lines(_LOG_FILE_PATH, [line])
        if to_merged:
            _append_lines(_MERGED_LOG_PATH, [line])
    except Exception:
        pass


def _should_log_verbose() -> bool:
    return _FULL_LOG_ENABLED or not _LIGHT_LOG


def _set_mitm_option(name: str, value: Any) -> None:
    try:
        if hasattr(ctx.options, name):
            setattr(ctx.options, name, value)
    except Exception:
        pass


def _apply_mitm_quiet_options() -> None:
    if not _MITM_QUIET:
        return
    _set_mitm_option("termlog_verbosity", _MITM_TERMLOG_VERBOSITY)
    _set_mitm_option("console_eventlog_verbosity", _MITM_EVENTLOG_VERBOSITY)
    _set_mitm_option("console_flowlist_verbosity", _MITM_FLOWLIST_VERBOSITY)
    _set_mitm_option("flow_detail", _MITM_FLOW_DETAIL)


def _capture_nethermind_logs() -> List[str]:
    global _NM_LAST_TS
    since_args: List[str] = ["--since", _NM_LAST_TS] if _NM_LAST_TS else ["--tail", "200"]
    try:
        cp = subprocess.run(
            ["docker", "logs", "--timestamps", *since_args, _NETHERMIND_CONTAINER],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if cp.returncode != 0:
            _log(f"[NM] docker logs rc={cp.returncode} out={cp.stdout[-200:]} err={cp.stderr or ''}")
            return []
        lines = [ln for ln in cp.stdout.splitlines() if ln.strip()]
        if lines:
            first = lines[-1]
            ts_part = first.split(" ", 1)[0]
            if ts_part:
                _NM_LAST_TS = ts_part
        return lines
    except Exception as e:
        _log(f"[NM] docker logs error: {e}")
        return []


def _emit_newpayload_event(exec_payload: Dict[str, Any], parent_hash: str) -> None:
    block_hash = exec_payload.get("blockHash") or "unknown"
    txs = exec_payload.get("transactions") or []
    block_number = exec_payload.get("blockNumber") or "unknown"
    summary = f"[MITM][NP] block={block_hash} blockNumber={block_number} parent={parent_hash} txs={len(txs)}"
    _log(summary, to_merged=True)

    nm_lines = _capture_nethermind_logs()
    if nm_lines:
        ts_prefix = _now_ts()
        prefixed = [f"{ts_prefix} [NM] {ln}" for ln in nm_lines]
        _append_lines(_MERGED_LOG_PATH, prefixed)
        _append_lines(_LOG_FILE_PATH, prefixed)
        if _FULL_LOG_ENABLED:
            _append_lines(_FULL_LOG_PATH, prefixed)


def _insert_empty_hook_separator(reason: str, scenario: str) -> None:
    global _SEPARATOR_READY_FOR_NEXT_SETUP, _PENDING_SEPARATOR_PAIR
    hook_block_hash = _read_hook_block_for_first_setup()
    if not hook_block_hash:
        _log(f"WARN cannot insert empty separator ({reason}): missing hook block")
        return
    hook_block = _rpc("eth_getBlockByHash", [hook_block_hash, False])
    if not isinstance(hook_block, dict):
        _log(f"WARN cannot insert empty separator ({reason}): hook block {hook_block_hash} not found")
        return

    parent_hash = hook_block.get("hash")
    if not isinstance(parent_hash, str) or not parent_hash:
        _log(f"WARN cannot insert empty separator ({reason}): hook parent missing hash")
        return

    parent_ts_hex = hook_block.get("timestamp")
    try:
        parent_ts = int(parent_ts_hex, 16) if isinstance(parent_ts_hex, str) else int(parent_ts_hex or 0)
    except Exception:
        parent_ts = int(time.time())

    separator_ts = _next_lifecycle_timestamp(parent_ts)
    extra_data = "0x4e65746865726d696e642076312e33372e3061"
    separator_attrs = {
        "timestamp": hex(separator_ts),
        "prevRandao": parent_hash,
        "suggestedFeeRecipient": "0x0000000000000000000000000000000000000000",
        "withdrawals": [],
        "parentBeaconBlockRoot": parent_hash,
    }

    _log(
        f"inserting empty hook separator ({reason}) scenario={scenario} parent={parent_hash} "
        f"timestamp={separator_attrs['timestamp']}"
    )
    sep_raw = _engine("testing_buildBlockV1", [parent_hash, separator_attrs, [], extra_data])
    sep_payload = sep_raw if isinstance(sep_raw, dict) else {}
    sep_exec = sep_payload.get("executionPayload", sep_payload)
    if not isinstance(sep_exec, dict):
        _log(f"WARN failed to insert empty separator ({reason}): non-dict payload")
        return

    sep_parent_hash = sep_exec.get("parentHash") or parent_hash
    sep_blob_hashes = _extract_blob_versioned_hashes(sep_payload, sep_exec)
    sep_exec_requests = _extract_execution_requests(sep_payload)
    _engine(_NEWPAYLOAD_METHOD, [sep_exec, sep_blob_hashes, separator_attrs["parentBeaconBlockRoot"], sep_exec_requests])
    _emit_newpayload_event(sep_exec, sep_parent_hash)
    sep_hash = sep_exec.get("blockHash")
    sep_dyn_final = _DYN_FINALIZED or FINALIZED_BLOCK or sep_hash
    sep_fcs = {
        "headBlockHash": sep_hash,
        "safeBlockHash": sep_dyn_final,
        "finalizedBlockHash": sep_dyn_final,
    }
    _engine("engine_forkchoiceUpdatedV3", [sep_fcs, None])
    np_body = {
        "jsonrpc": "2.0",
        "id": int(time.time()),
        "method": _NEWPAYLOAD_METHOD,
        "params": [sep_exec, sep_blob_hashes, separator_attrs["parentBeaconBlockRoot"], sep_exec_requests],
    }
    fcu_body = {
        "jsonrpc": "2.0",
        "id": int(time.time()),
        "method": "engine_forkchoiceUpdatedV3",
        "params": [sep_fcs, None],
    }
    _PENDING_SEPARATOR_PAIR = (np_body, fcu_body)
    _SEPARATOR_READY_FOR_NEXT_SETUP = True
    _log(f"inserted empty hook separator ({reason}) hash={sep_hash}")


def _http_post_json(url: str, obj: Any, timeout: int = 90, headers: Optional[Dict[str, str]] = None) -> Any:
    try:
        import requests  # type: ignore
        r = requests.post(url, json=obj, timeout=timeout, headers=headers or {})
        r.raise_for_status()
        return r.json()
    except Exception:
        import urllib.request
        data = json.dumps(obj).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json", **(headers or {})}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))


def _b64url(b: bytes) -> bytes:
    return base64.urlsafe_b64encode(b).rstrip(b"=")


def _jwt_from_file() -> str:
    secret_hex = open(JWT_HEX_PATH, "r").read().strip().replace("0x", "")
    secret = bytes.fromhex(secret_hex)
    header = _b64url(b'{"alg":"HS256","typ":"JWT"}')
    payload = _b64url(json.dumps({"iat": int(time.time())}).encode())
    unsigned = header + b"." + payload
    sig = hmac.new(secret, unsigned, hashlib.sha256).digest()
    return (unsigned + b"." + _b64url(sig)).decode()


def _engine(method: str, params: List[Any]) -> Any:
    token = _jwt_from_file()
    body = {"jsonrpc": "2.0", "id": int(time.time()), "method": method, "params": params}

    if _should_log_verbose():
        # Log with redacted Authorization
        eng_hdrs = {"Content-Type": "application/json", "Authorization": "Bearer <redacted>"}
        _log(
            f"REQ POST {_ENGINE_HOST}:{_ENGINE_PORT}{_ENGINE_PATH} "
            f"headers={json.dumps(eng_hdrs)} body_preview={json.dumps(body)}"
        )

    j = _http_post_json(ENGINE_URL, body, timeout=90, headers={"Authorization": f"Bearer {token}"})
    if _should_log_verbose():
        try:
            _log(
                f"RESP POST {_ENGINE_HOST}:{_ENGINE_PORT}{_ENGINE_PATH} "
                f"status=200 headers={json.dumps({'Content-Type':'application/json'})} "
                f"body_preview={json.dumps(j)}"
            )
        except Exception:
            _log(f"RESP POST {_ENGINE_HOST}:{_ENGINE_PORT}{_ENGINE_PATH} status=200 <non-json>")

    if "error" in j:
        _log(f"ERROR engine call {method} failed: {j['error']}")
        raise RuntimeError(str(j["error"]))

    return j["result"]


def _rpc(method: str, params: Optional[List[Any]] = None) -> Any:
    try:
        j = _http_post_json(
            RPC_DIRECT,
            {"jsonrpc": "2.0", "id": int(time.time()), "method": method, "params": params or []},
            timeout=30,
        )
        return j.get("result")
    except Exception as e:
        _log(f"rpc {method} failed: {e}")
        return None


def _sanitize_filename_component(s: str) -> str:
    # Keep name readable; only neutralize path separators and control chars.
    s = s.replace(os.sep, "_").replace("\\", "_").replace("/", "_")
    s = s.replace("\x00", "").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    s = s.strip()
    return s or "unknown"


def _parse_id_json(id_obj: Any) -> Optional[Dict[str, Any]]:
    if isinstance(id_obj, str):
        try:
            id_obj = json.loads(id_obj)
        except Exception:
            return None
    if isinstance(id_obj, dict):
        return {"testId": id_obj.get("testId"), "phase": id_obj.get("phase"), "txIndex": id_obj.get("txIndex")}
    return None


def _parse_header_json(hdr_str: Optional[str]) -> Optional[Dict[str, Any]]:
    if not hdr_str:
        return None
    try:
        j = json.loads(hdr_str)
        if isinstance(j, dict):
            return {"testId": j.get("testId"), "phase": j.get("phase"), "txIndex": j.get("txIndex")}
    except Exception:
        return None
    return None


def _derive_group_from_meta(meta: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    test_id = (meta or {}).get("testId") or "unknown"
    if "::" in test_id:
        file_path_str, test_name = test_id.split("::", 1)
    else:
        file_path_str, test_name = test_id, "unknown_test"
    file_base = _sanitize_filename_component(os.path.basename(file_path_str))
    test_name = _sanitize_filename_component(test_name)
    phase = _sanitize_filename_component((meta or {}).get("phase") or "unknown")
    return (file_base, test_name, phase)


def _extra_data_label_from_meta(meta: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(meta, dict):
        return None
    test_id = meta.get("testId")
    if not isinstance(test_id, str) or not test_id:
        return None
    phase = meta.get("phase")
    if isinstance(phase, str) and phase:
        return f"{phase}:{test_id}"
    return test_id


def _extra_data_from_label(label: Optional[str]) -> str:
    # Keep extraData human-readable in client logs; header remains consensus-hashed as usual.
    if not isinstance(label, str) or not label:
        label = "Nethermind v1.37.0a"
    # Ensure printable, stable ASCII before truncating to the 32-byte header limit.
    ascii_text = "".join(ch if 32 <= ord(ch) <= 126 else "_" for ch in label)
    data = ascii_text.encode("ascii", errors="ignore")[:32]
    return "0x" + data.hex()


def _keccak256(data: bytes) -> bytes:
    try:
        from eth_hash.auto import keccak as _eth_keccak  # type: ignore
        return _eth_keccak(data)
    except Exception:
        pass
    try:
        import sha3  # type: ignore
        k = sha3.keccak_256()
        k.update(data)
        return k.digest()
    except Exception:
        pass
    try:
        from Crypto.Hash import keccak as _crypto_keccak  # type: ignore
        k = _crypto_keccak.new(digest_bits=256)
        k.update(data)
        return k.digest()
    except Exception:
        pass
    raise RuntimeError("No Keccak-256 implementation available for tx hash calculation")


def _tx_hash_from_raw(raw_tx: str) -> str:
    raw_bytes = _hex_to_bytes(raw_tx)
    if raw_bytes is None:
        raise ValueError("Invalid raw tx hex")
    try:
        return "0x" + _keccak256(raw_bytes).hex()
    except Exception as local_err:
        # Fallback for minimal runner environments: ask node for Keccak(data).
        raw_hex = raw_tx if isinstance(raw_tx, str) and raw_tx.startswith("0x") else ("0x" + raw_bytes.hex())
        rpc_hash = _rpc("web3_sha3", [raw_hex])
        if isinstance(rpc_hash, str) and rpc_hash.startswith("0x") and len(rpc_hash) == 66:
            return rpc_hash
        raise RuntimeError(
            f"No local Keccak-256 implementation and web3_sha3 fallback failed: {local_err}"
        )


def _sendraw_success_response(req_obj: Dict[str, Any], tx_hash: str) -> Dict[str, Any]:
    return {
        "jsonrpc": req_obj.get("jsonrpc", "2.0"),
        "id": req_obj.get("id"),
        "result": tx_hash,
    }


def _sendraw_error_response(req_obj: Dict[str, Any], message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": req_obj.get("jsonrpc", "2.0"),
        "id": req_obj.get("id"),
        "error": {"code": -32602, "message": message},
    }


def _scenario_name(file_base: str, test_name: str) -> str:
    fb = _sanitize_filename_component(file_base)
    tn = _sanitize_filename_component(test_name)

    suffix = ""
    match = re.search(r"-benchmark-gas-value_([^-]+)", tn)
    if match:
        value = _sanitize_filename_component(match.group(1))
        tn = re.sub(r"-benchmark-gas-value_[^-]+", "-benchmark", tn, count=1)
        suffix = f"_{value}" if value else ""

    scenario = f"{fb}__{tn}{suffix}"
    return scenario


def _collect_hashes_from_node(node: Any) -> List[str]:
    hashes: List[str] = []
    if isinstance(node, dict):
        if "hash" in node and isinstance(node.get("hash"), str):
            hashes.append(node["hash"])
        for v in node.values():
            hashes.extend(_collect_hashes_from_node(v))
    elif isinstance(node, list):
        for v in node:
            hashes.extend(_collect_hashes_from_node(v))
    return hashes


def _log_txpool_summary() -> None:
    pool = _rpc("txpool_content")
    if pool is None:
        _log("txpool_content: None")
        return
    try:
        pending = pool.get("pending") if isinstance(pool, dict) else None
        queued = pool.get("queued") if isinstance(pool, dict) else None

        def _count(node: Any) -> int:
            if node is None:
                return 0
            if isinstance(node, dict):
                total = 0
                for v in node.values():
                    total += _count(v)
                if "hash" in node and isinstance(node.get("hash"), str):
                    total = max(total, 1)
                return total
            if isinstance(node, list):
                return sum(_count(v) for v in node)
            return 0

        p_cnt = _count(pending)
        q_cnt = _count(queued)
        _log(f"txpool_content pending={p_cnt} queued={q_cnt}")

        first_hashes = _collect_hashes_from_node(pending)[:3] if pending is not None else []
        if first_hashes:
            _log(f"txpool_content sample={', '.join(first_hashes)}")
    except Exception as e:
        _log(f"txpool summarize error: {e}")


def _hex_to_bytes(value: Any) -> Optional[bytes]:
    if not isinstance(value, str):
        return None
    v = value[2:] if value.startswith("0x") else value
    if len(v) % 2 == 1:
        v = "0" + v
    try:
        return bytes.fromhex(v)
    except Exception:
        return None


def _kzg_commitment_to_versioned_hash(commitment_hex: Any) -> Optional[str]:
    raw = _hex_to_bytes(commitment_hex)
    if raw is None:
        return None
    digest = hashlib.sha256(raw).digest()
    versioned = b"\x01" + digest[1:]
    return "0x" + versioned.hex()


def _extract_blob_versioned_hashes(payload: Dict[str, Any], exec_payload: Dict[str, Any]) -> List[str]:
    for key in ("blobVersionedHashes", "blob_versioned_hashes", "versionedHashes", "versioned_hashes"):
        hashes = payload.get(key)
        if isinstance(hashes, list) and hashes:
            return [h for h in hashes if isinstance(h, str)]

    bundle = payload.get("blobsBundle") or payload.get("blobs_bundle") or {}
    commitments = None
    if isinstance(bundle, dict):
        commitments = bundle.get("commitments")
    if isinstance(commitments, list) and commitments:
        computed: List[str] = []
        for c in commitments:
            vh = _kzg_commitment_to_versioned_hash(c)
            if vh:
                computed.append(vh)
        if computed:
            return computed

    return []


def _extract_execution_requests(payload: Dict[str, Any]) -> List[Any]:
    for key in ("executionRequests", "execution_requests"):
        reqs = payload.get(key)
        if isinstance(reqs, list):
            return reqs
    return []


def _extract_parent_beacon_block_root(payload: Dict[str, Any], exec_payload: Dict[str, Any]) -> Optional[str]:
    for key in ("parentBeaconBlockRoot", "parent_beacon_block_root"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    for key in ("parentBeaconBlockRoot", "parent_beacon_block_root"):
        val = exec_payload.get(key)
        if isinstance(val, str) and val:
            return val
    return None


# ---------------------------------------------------------------------------
# File IO helpers for new layout
# ---------------------------------------------------------------------------

def _ensure_dirs_and_cleanup_old() -> None:
    global _LEGACY_PHASE_DIRS_CLEANED
    _PAYLOADS_DIR.mkdir(parents=True, exist_ok=True)
    _SETUP_DIR.mkdir(parents=True, exist_ok=True)
    _TESTING_DIR.mkdir(parents=True, exist_ok=True)
    _CLEANUP_DIR.mkdir(parents=True, exist_ok=True)

    # One-time cleanup: remove legacy numbered subdirectories from old layout.
    if _LEGACY_PHASE_DIRS_CLEANED:
        return
    _LEGACY_PHASE_DIRS_CLEANED = True

    for phase_dir in (_SETUP_DIR, _TESTING_DIR, _CLEANUP_DIR):
        try:
            for child in phase_dir.iterdir():
                if child.is_dir() and child.name.isdigit():
                    try:
                        shutil.rmtree(child, ignore_errors=True)
                        _log(f"removed legacy numbered scenario dir: {child}")
                    except Exception as exc:
                        _log(f"failed to remove legacy scenario dir {child}: {exc}")
        except Exception:
            pass


def _minified_json_line(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))


def _next_lifecycle_timestamp(parent_ts: int) -> int:
    global _LIFECYCLE_TS
    if _LIFECYCLE_TS is None:
        _LIFECYCLE_TS = parent_ts + 1
    else:
        _LIFECYCLE_TS += 1
    # Safety: always keep timestamp valid vs chosen parent.
    if _LIFECYCLE_TS <= parent_ts:
        _LIFECYCLE_TS = parent_ts + 1
    return _LIFECYCLE_TS


def _read_hook_block_for_first_setup() -> Optional[str]:
    # Preferred hook source: dedicated empty hook anchor.
    hook_anchor = (HOOK_BLOCK or "").strip()
    if hook_anchor:
        return hook_anchor

    # Fallback: funding anchor passed by generator config.
    funding_anchor = (FINALIZED_BLOCK or "").strip()
    if funding_anchor:
        return funding_anchor

    # Legacy fallback: derive hook from setup-global-test payload file.
    if not _SETUP_GLOBAL_FILE.exists():
        return None
    try:
        last_block_hash: Optional[str] = None
        lines = _SETUP_GLOBAL_FILE.read_text(encoding="utf-8").splitlines()
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            method = obj.get("method")
            if not isinstance(method, str) or not method.startswith("engine_newPayload"):
                continue
            params = obj.get("params") or []
            if params and isinstance(params[0], dict):
                block_hash = params[0].get("blockHash")
                if isinstance(block_hash, str) and block_hash:
                    last_block_hash = block_hash
        return last_block_hash
    except Exception as e:
        _log(f"hook block read failed from {_SETUP_GLOBAL_FILE}: {e}")
        return None


def _append_line(path: pathlib.Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.write("\n")


def _overwrite_with_lines(path: pathlib.Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln)
            f.write("\n")
    tmp.replace(path)


def _truncate(path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8"):
        pass


def _truncate_if_first_seen(scenario: str) -> None:
    if scenario in _SEEN_SCENARIOS:
        return
    idx = _register_scenario(scenario)
    _SEEN_SCENARIOS.add(scenario)
    _TESTING_SEEN_COUNT[scenario] = 0
    _log(f"initialized scenario index {idx} for {scenario}")


def _dump_pair_to_phase(phase: str, scenario: str, np_body: Dict[str, Any], fcu_body: Dict[str, Any]) -> None:
    np_line = _minified_json_line(np_body)
    fcu_line = _minified_json_line(fcu_body)

    if phase == "setup":
        setup_path = _scenario_file_path("setup", scenario)
        _append_line(setup_path, np_line)
        _append_line(setup_path, fcu_line)
        _log(f"setup append → {setup_path}")
        return

    if phase == "cleanup":
        cleanup_path = _scenario_file_path("cleanup", scenario)
        _append_line(cleanup_path, np_line)
        _append_line(cleanup_path, fcu_line)
        _log(f"cleanup append → {cleanup_path}")
        return

    # testing
    count = _TESTING_SEEN_COUNT.get(scenario, 0)
    testing_path = _scenario_file_path("testing", scenario)
    setup_path = _scenario_file_path("setup", scenario)

    if EEST_STATEFUL_TESTING:
        _append_line(testing_path, np_line)
        _append_line(testing_path, fcu_line)
        _log(f"testing append (stateful) → {testing_path}")
    elif count == 0:
        _overwrite_with_lines(testing_path, [np_line, fcu_line])
        _log(f"testing write (first) → {testing_path}")
    else:
        if testing_path.exists():
            try:
                with testing_path.open("r", encoding="utf-8") as f:
                    prev_lines = [ln.rstrip("\n") for ln in f if ln.strip() != ""]
                for ln in prev_lines:
                    _append_line(setup_path, ln)
                _log(f"testing migrate {len(prev_lines)} line(s) → {setup_path}")
            except Exception as e:
                _log(f"migrate testing→setup failed: {e}")
        _overwrite_with_lines(testing_path, [np_line, fcu_line])
        _log(f"testing overwrite (latest) → {testing_path}")

    _TESTING_SEEN_COUNT[scenario] = count + 1


# ---- helpers for global no-phase routing ----------------------------------

def _append_pair(path: pathlib.Path, np_body: Dict[str, Any], fcu_body: Dict[str, Any]) -> None:
    _append_line(path, _minified_json_line(np_body))
    _append_line(path, _minified_json_line(fcu_body))

def _file_has_content(path: pathlib.Path) -> bool:
    try:
        return path.exists() and path.stat().st_size > 0
    except Exception:
        return False

def _migrate_current_last_to_middle() -> None:
    if _file_has_content(_CURRENT_LAST_FILE):
        try:
            with _CURRENT_LAST_FILE.open("r", encoding="utf-8") as f:
                lines = [ln.rstrip("\n") for ln in f if ln.strip()]
            for ln in lines:
                _append_line(_MIDDLE_GLOBAL_FILE, ln)
            _truncate(_CURRENT_LAST_FILE)
            _log(f"migrated {len(lines)} line(s) current-last → { _MIDDLE_GLOBAL_FILE }")
        except Exception as e:
            _log(f"migrate current-last→middle failed: {e}")

def _cleanup_empty_txt_files() -> None:
    try:
        phase_dirs = {_SETUP_DIR.resolve(), _TESTING_DIR.resolve(), _CLEANUP_DIR.resolve()}
        for p in _PAYLOADS_DIR.rglob("*.txt"):
            try:
                if p.exists() and p.stat().st_size == 0:
                    parent = p.parent.resolve()
                    if parent in phase_dirs:
                        continue
                    p.unlink()
                    _log(f"removed empty file: {p}")
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Flushing / Production
# ---------------------------------------------------------------------------

def _flush_group(grp: Tuple[str, str, str] | None, txrlps: List[str], last_extra_data_label: Optional[str] = None) -> None:
    if not txrlps:
        _log(f"flush skipped: empty buffer for group={grp}")
        return

    try:
        first = txrlps[0]
        last = txrlps[-1]
        preview_first = (first[:18] + "…" + first[-10:]) if isinstance(first, str) else str(first)[:32]
        preview_last = (last[:18] + "…" + last[-10:]) if isinstance(last, str) else str(last)[:32]

        file_base, test_name, phase = grp or ("unknown", "unknown", "unknown")
        scenario = _scenario_name(file_base, test_name)

        _log(
            f"GETPAYLOAD group={grp} count={len(txrlps)} first={preview_first} "
            f"last={preview_last} reorg=false"
        )

        next_stage = _STAGE.get(grp, 0) + 1
        phase_lc = (phase or "").lower()
        is_first_setup_for_scenario = (
            phase_lc == "setup"
            and next_stage == 1
            and file_base not in {"global-setup", "global-nophase"}
        )

        latest_block = _rpc("eth_getBlockByNumber", ["latest", False])
        if not isinstance(latest_block, dict):
            _log(f"flush failed: could not fetch latest block for group={grp}")
            return

        parent_block: Dict[str, Any] = latest_block
        parent_source = "latest"
        use_preinserted_separator = is_first_setup_for_scenario and _SEPARATOR_READY_FOR_NEXT_SETUP
        if use_preinserted_separator:
            globals()["_SEPARATOR_READY_FOR_NEXT_SETUP"] = False
            _log(f"using pre-inserted separator parent for scenario={scenario}")
        elif is_first_setup_for_scenario:
            hook_block_hash = _read_hook_block_for_first_setup()
            if hook_block_hash:
                hook_block = _rpc("eth_getBlockByHash", [hook_block_hash, False])
                if isinstance(hook_block, dict):
                    parent_block = hook_block
                    parent_source = "hook"
                else:
                    _log(f"WARN HOOK_BLOCK {hook_block_hash} not found on node; using latest")
            else:
                _log("WARN HOOK_BLOCK not found (hook/funding/setup-global fallback); using latest")

        parent_hash = parent_block.get("hash")
        if not isinstance(parent_hash, str) or not parent_hash:
            _log(f"flush failed: parent block missing hash for group={grp} source={parent_source}")
            return

        extra_data = _extra_data_from_label(last_extra_data_label)

        parent_ts_hex = parent_block.get("timestamp")
        try:
            parent_ts = int(parent_ts_hex, 16) if isinstance(parent_ts_hex, str) else int(parent_ts_hex or 0)
        except Exception:
            parent_ts = int(time.time())

        inline_separator_pair: Optional[Tuple[Dict[str, Any], Dict[str, Any]]] = None
        if is_first_setup_for_scenario and parent_source == "hook":
            separator_ts = _next_lifecycle_timestamp(parent_ts)
            separator_attrs = {
                "timestamp": hex(separator_ts),
                "prevRandao": parent_hash,
                "suggestedFeeRecipient": "0x0000000000000000000000000000000000000000",
                "withdrawals": [],
                "parentBeaconBlockRoot": parent_hash,
            }
            _log(f"inserting empty hook separator block before scenario={scenario} parent={parent_hash}")
            sep_raw = _engine("testing_buildBlockV1", [parent_hash, separator_attrs, [], extra_data])
            sep_payload = sep_raw if isinstance(sep_raw, dict) else {}
            sep_exec = sep_payload.get("executionPayload", sep_payload)
            if isinstance(sep_exec, dict):
                sep_parent_hash = sep_exec.get("parentHash") or parent_hash
                sep_blob_hashes = _extract_blob_versioned_hashes(sep_payload, sep_exec)
                sep_exec_requests = _extract_execution_requests(sep_payload)
                _engine(_NEWPAYLOAD_METHOD, [sep_exec, sep_blob_hashes, separator_attrs["parentBeaconBlockRoot"], sep_exec_requests])
                _emit_newpayload_event(sep_exec, sep_parent_hash)
                sep_hash = sep_exec.get("blockHash")
                sep_dyn_final = _DYN_FINALIZED or FINALIZED_BLOCK or sep_hash
                sep_fcs = {
                    "headBlockHash": sep_hash,
                    "safeBlockHash": sep_dyn_final,
                    "finalizedBlockHash": sep_dyn_final,
                }
                _engine("engine_forkchoiceUpdatedV3", [sep_fcs, None])
                inline_separator_pair = (
                    {
                        "jsonrpc": "2.0",
                        "id": int(time.time()),
                        "method": _NEWPAYLOAD_METHOD,
                        "params": [sep_exec, sep_blob_hashes, separator_attrs["parentBeaconBlockRoot"], sep_exec_requests],
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": int(time.time()),
                        "method": "engine_forkchoiceUpdatedV3",
                        "params": [sep_fcs, None],
                    },
                )
                sep_block = _rpc("eth_getBlockByHash", [sep_hash, False]) if isinstance(sep_hash, str) and sep_hash else None
                if not isinstance(sep_block, dict):
                    sep_block = _rpc("eth_getBlockByNumber", ["latest", False])
                if isinstance(sep_block, dict):
                    parent_block = sep_block
                    parent_source = "hook-empty"
                    _log(f"inserted empty hook separator block hash={sep_hash}")
                else:
                    _log("WARN inserted separator block but failed to reload latest parent; proceeding")
            else:
                _log(f"WARN failed to insert hook separator for scenario={scenario}: non-dict payload")

        parent_hash = parent_block.get("hash")
        if not isinstance(parent_hash, str) or not parent_hash:
            _log(f"flush failed: effective parent block missing hash for group={grp} source={parent_source}")
            return

        parent_ts_hex = parent_block.get("timestamp")
        try:
            parent_ts = int(parent_ts_hex, 16) if isinstance(parent_ts_hex, str) else int(parent_ts_hex or 0)
        except Exception:
            parent_ts = int(time.time())
        new_ts = _next_lifecycle_timestamp(parent_ts)

        payload_attributes = {
            "timestamp": hex(new_ts),
            "prevRandao": parent_hash,
            "suggestedFeeRecipient": "0x0000000000000000000000000000000000000000",
            "withdrawals": [],
            "parentBeaconBlockRoot": parent_hash,
        }
        _log(
            f"buildBlock parent source={parent_source} hash={parent_hash} phase={phase_lc} stage={next_stage} "
            f"extraData={extra_data}"
        )

        exec_payload_raw = _engine("testing_buildBlockV1", [parent_hash, payload_attributes, txrlps, extra_data])
        payload = exec_payload_raw if isinstance(exec_payload_raw, dict) else {}
        exec_payload = payload.get("executionPayload", payload)
        if not isinstance(exec_payload, dict):
            _log(f"flush failed: testing_buildBlockV1 returned non-dict payload for group={grp}")
            return

        parent_hash = exec_payload.get("parentHash") or parent_hash
        blob_versioned_hashes = _extract_blob_versioned_hashes(payload, exec_payload)
        parent_beacon_block_root = payload_attributes["parentBeaconBlockRoot"]
        execution_requests = _extract_execution_requests(payload)
        blob_gas_used = exec_payload.get("blobGasUsed")
        if blob_versioned_hashes == [] and blob_gas_used not in (None, 0, "0x0", "0x00"):
            _log("WARN blobGasUsed present but no blobVersionedHashes found")

        _STAGE[grp] = next_stage
        idx = next_stage

        np_body = {
            "jsonrpc": "2.0",
            "id": int(time.time()),
            "method": _NEWPAYLOAD_METHOD,
            "params": [exec_payload, blob_versioned_hashes, parent_beacon_block_root, execution_requests],
        }

        _engine(_NEWPAYLOAD_METHOD, [exec_payload, blob_versioned_hashes, parent_beacon_block_root, execution_requests])
        _emit_newpayload_event(exec_payload, parent_hash)

        if file_base == "global-setup":
            # keep anchor update logic for historical behavior
            old = _DYN_FINALIZED
            _globals = globals()
            _globals["_DYN_FINALIZED"] = exec_payload.get("blockHash") or old
            if _globals["_DYN_FINALIZED"] != old:
                _log(f"FINALIZED anchor updated by global-setup → {_globals['_DYN_FINALIZED']}")

        dyn_final = _DYN_FINALIZED or FINALIZED_BLOCK or exec_payload.get("blockHash")
        fcs = {
            "headBlockHash": exec_payload.get("blockHash"),
            "safeBlockHash": dyn_final,
            "finalizedBlockHash": dyn_final,
        }
        fcu_body = {
            "jsonrpc": "2.0",
            "id": int(time.time()),
            "method": "engine_forkchoiceUpdatedV3",
            "params": [fcs, None],
        }

        _engine("engine_forkchoiceUpdatedV3", [fcs, None])

        # Ensure base dirs exist
        _ensure_dirs_and_cleanup_old()

        # ---- GLOBAL NO-PHASE (both legacy 'global-setup' and explicit 'global-nophase') ----
        if file_base in {"global-setup", "global-nophase"}:
            if REUSE_GLOBALS and _file_has_content(_SETUP_GLOBAL_FILE):
                _log(f"global-no-phase reuse active; skipping file updates for {grp}")
            else:
                if not _TESTS_STARTED:
                    # Before any phased test started -> setup-global-test
                    _append_pair(_SETUP_GLOBAL_FILE, np_body, fcu_body)
                    _log(f"global-no-phase PRE-TEST -> {_SETUP_GLOBAL_FILE}")
                else:
                    # During/after tests: roll current-last
                    if _file_has_content(_CURRENT_LAST_FILE):
                        _migrate_current_last_to_middle()
                    _overwrite_with_lines(_CURRENT_LAST_FILE, [
                        _minified_json_line(np_body),
                        _minified_json_line(fcu_body)
                    ])
                    _log(f"global-no-phase CURRENT-LAST updated -> {_CURRENT_LAST_FILE}")
            if not DISABLE_OVERLAY_RESTORE and not _OVERLAY_PRIMED and not _TESTS_STARTED:
                block_hash = exec_payload.get("blockHash")
                globals()["_PENDING_OVERLAY"] = ("__overlay_init__", idx, block_hash)
                _signal_cleanup_pause("__overlay_init__", idx, block_hash)
                _log("global-no-phase overlay init pause triggered")
                globals()['_OVERLAY_PRIMED'] = True
            _log(f"produced block group={grp} stage={idx}")
            return
        # -------------------------------------------------------------------------------------

        # Normal scenario flow
        _truncate_if_first_seen(scenario)
        ph = phase.lower()
        if ph not in {"setup", "testing", "cleanup"}:
            _log(f"unknown phase '{phase}' -> treating as 'setup' for dump")
            ph = "setup"

        if ph == "setup" and idx == 1:
            pending_separator_pair = globals().get("_PENDING_SEPARATOR_PAIR")
            if isinstance(pending_separator_pair, tuple) and len(pending_separator_pair) == 2:
                sep_np_body, sep_fcu_body = pending_separator_pair
                _dump_pair_to_phase("setup", scenario, sep_np_body, sep_fcu_body)
                globals()["_PENDING_SEPARATOR_PAIR"] = None
                _log(f"setup prepended pre-inserted hook separator for scenario={scenario}")
            elif inline_separator_pair is not None:
                sep_np_body, sep_fcu_body = inline_separator_pair
                _dump_pair_to_phase("setup", scenario, sep_np_body, sep_fcu_body)
                _log(f"setup prepended inline hook separator for scenario={scenario}")

        _dump_pair_to_phase(ph, scenario, np_body, fcu_body)
        _log(f"produced block group={grp} stage={idx}")
        if ph == "cleanup":
            block_hash = exec_payload.get("blockHash")
            globals()['_PENDING_OVERLAY'] = None
            _clear_pending_tx_hashes()
            if not DISABLE_OVERLAY_RESTORE:
                _log(f"cleanup stage {idx} complete for {scenario}; triggering immediate restore")
                _signal_cleanup_pause("__overlay_restore__", idx, block_hash)
                _wait_for_resume()
                _insert_empty_hook_separator("cleanup-stage", scenario)
            else:
                _log(f"cleanup stage {idx} complete for {scenario}; overlay restore disabled")
        elif SKIP_CLEANUP and not DISABLE_OVERLAY_RESTORE and ph == "testing":
            block_hash = exec_payload.get("blockHash")
            globals()['_PENDING_OVERLAY'] = (scenario, idx, block_hash)
            tx_hashes: set = set()
            for txrlp in txrlps:
                try:
                    tx_hashes.add(_tx_hash_from_raw(txrlp).lower())
                except Exception:
                    pass
            with _PENDING_TX_LOCK:
                globals()['_PENDING_TX_HASHES'] = tx_hashes
                if tx_hashes:
                    _ALL_TX_CONFIRMED_EVENT.clear()
                else:
                    _ALL_TX_CONFIRMED_EVENT.set()
            _log(
                f"testing stage {idx} complete for {scenario}; restore deferred until "
                f"all {len(tx_hashes)} tx(s) confirmed via eth_getTransactionByHash (--skip-cleanup)"
            )
    except Exception as e:  # pragma: no cover
        _log(f"produce error: {e}")


def _force_flush_buffer() -> None:
    """Drain the pending buffer and build a block immediately."""
    global _PENDING, _BUF
    with _GROUP_LOCK:
        if not _PENDING:
            return
        grp = _ACTIVE_GRP
        buf_copy = list(_BUF)
        _PENDING = False
        _BUF = []
    # Sort by txIndex to ensure proper transaction ordering within a block.
    # Transactions without a txIndex keep their arrival order at the end.
    buf_copy.sort(key=lambda x: (x[3] is None, x[3] if x[3] is not None else 0))
    _log(f"flushing group={grp} size={len(buf_copy)} reason=force")
    if buf_copy:
        last_extra_data_label = buf_copy[-1][2]
        _flush_group(grp, [x[0] for x in buf_copy], last_extra_data_label=last_extra_data_label)


def _has_pending_buffered_sendraw() -> bool:
    with _GROUP_LOCK:
        return _PENDING and bool(_BUF)


# ---------------------------------------------------------------------------
# mitmproxy addon hooks
# ---------------------------------------------------------------------------

def _ensure_control_dir() -> None:
    try:
        _CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _signal_cleanup_pause(scenario: str, stage: int, block_hash: Optional[str]) -> None:
    global _PAUSE_TOKEN, _PAUSE_SCENARIO
    with _PAUSE_LOCK:
        if not _PAUSE_EVENT.is_set():
            _log(f"pause already active; skip scenario={scenario}")
            return
        token = str(uuid.uuid4())
        _PAUSE_TOKEN = token
        _PAUSE_SCENARIO = scenario
        _PAUSE_EVENT.clear()
        _ensure_control_dir()
        payload = {
            "token": token,
            "scenario": scenario,
            "phase": "cleanup",
            "stage": stage,
            "blockHash": block_hash,
            "timestamp": time.time(),
        }
        try:
            _PAUSE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as e:
            _log(f"pause write failed: {e}")
        _log(f"pause signaled scenario={scenario} stage={stage} token={token}")


def _control_watcher() -> None:
    while not _STOP:
        if _PAUSE_EVENT.is_set():
            time.sleep(0.05)
            continue
        if not _RESUME_FILE.exists():
            time.sleep(0.05)
            continue
        try:
            data = json.loads(_RESUME_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            _log(f"resume read failed: {e}")
            time.sleep(0.05)
            continue
        token = data.get("token")
        scenario = data.get("scenario")
        with _PAUSE_LOCK:
            if not _PAUSE_EVENT.is_set() and token == _PAUSE_TOKEN and scenario == _PAUSE_SCENARIO:
                _PAUSE_EVENT.set()
                _clear_pending_tx_hashes()
                _log(f"resume accepted scenario={scenario} token={token}")
                if scenario == "__overlay_init__":
                    globals()["_PENDING_OVERLAY"] = None
                try:
                    _RESUME_FILE.unlink()
                except Exception:
                    pass
            else:
                _log(f"resume ignored token={token} scenario={scenario}")
        time.sleep(0.05)


def _wait_for_resume() -> None:
    while not _PAUSE_EVENT.wait(timeout=0.05):
        if _STOP:
            return
        time.sleep(0.02)


def _wait_for_all_tx_confirmed() -> None:
    """Block until all pending testing-phase tx hashes are confirmed."""
    while not _ALL_TX_CONFIRMED_EVENT.wait(timeout=0.05):
        if _STOP:
            return
        time.sleep(0.02)


def _clear_pending_tx_hashes() -> None:
    """Clear pending tx hash tracking and release any waiters."""
    with _PENDING_TX_LOCK:
        globals()['_PENDING_TX_HASHES'] = set()
        _ALL_TX_CONFIRMED_EVENT.set()


def load(loader) -> None:
    global _CONTROL_THREAD
    _apply_mitm_quiet_options()
    _log(
        "timestamp mode for testing_buildBlockV1: "
        + ("parent+24h+1 (hack enabled)" if _TESTING_BUILDBLOCK_TIMESTAMP_HACK else "parent+1 (default)")
    )
    if DISABLE_OVERLAY_RESTORE:
        _log("OVERLAY RESTORE DISABLED — no pause/resume or reorg between scenarios")
    else:
        _log(f"overlay restore trigger: eth_getBalance for {OVERLAY_RESTORE_TRIGGER_ADDRESS}")
    _ensure_dirs_and_cleanup_old()
    globals()['_OVERLAY_PRIMED'] = False
    globals()['_PENDING_OVERLAY'] = None
    _clear_pending_tx_hashes()
    _ensure_control_dir()
    try:
        if _RESUME_FILE.exists():
            _RESUME_FILE.unlink()
    except Exception:
        pass
    try:
        if _PAUSE_FILE.exists():
            _PAUSE_FILE.unlink()
    except Exception:
        pass
    _PAUSE_EVENT.set()

    files_to_truncate = [_CURRENT_LAST_FILE]
    if not REUSE_GLOBALS:
        files_to_truncate.extend([_SETUP_GLOBAL_FILE, _MIDDLE_GLOBAL_FILE])
    for p in files_to_truncate:
        _truncate(p)

    if _GLOBAL_SETUP_FILE.exists():
        try:
            lines = [ln.strip() for ln in _GLOBAL_SETUP_FILE.read_text(encoding='utf-8').splitlines() if ln.strip()]
            if lines:
                if len(lines) % 2 != 0:
                    _log(f"[warn] global-setup.txt has odd number of lines ({len(lines)}); expected NP+FCU pairs")
                pairs = [lines[i:i+2] for i in range(0, len(lines) - len(lines) % 2, 2)]
                if pairs:
                    for ln in pairs[0]:
                        _append_line(_SETUP_GLOBAL_FILE, ln)
                    for pair in pairs[1:-1]:
                        for ln in pair:
                            _append_line(_MIDDLE_GLOBAL_FILE, ln)
                    if len(pairs) > 1:
                        for ln in pairs[-1]:
                            _append_line(_MIDDLE_GLOBAL_FILE, ln)
                    _GLOBAL_SETUP_FILE.rename(_GLOBAL_SETUP_FILE.with_suffix('.migrated.bak'))
                    _log(f"migrated {len(pairs)} global-setup pair(s) into lifecycle files; backed up to global-setup.migrated.bak")
        except Exception as e:
            _log(f"migration of global-setup.txt failed: {e}")

    stray = _SETUP_DIR / 'global-setup__global-setup.txt'
    if stray.exists():
        try:
            with stray.open('r', encoding='utf-8') as f_in:
                content = f_in.read()
            with _GLOBAL_SETUP_FILE.open('a', encoding='utf-8') as f_out:
                f_out.write(content)
            stray.unlink()
            _log(f"moved stray {stray} into {_GLOBAL_SETUP_FILE}")
        except Exception as e:
            _log(f"migration of {stray} failed: {e}")

    _CONTROL_THREAD = threading.Thread(target=_control_watcher, daemon=True)
    _CONTROL_THREAD.start()
    _log('mitm_addon loaded')

def done() -> None:
    global _STOP, _CONTROL_THREAD
    # Remove any leftover empty .txt files (root and subdirs)
    _cleanup_empty_txt_files()

    _STOP = True
    _PAUSE_EVENT.set()
    if _CONTROL_THREAD and _CONTROL_THREAD.is_alive():
        _CONTROL_THREAD.join(timeout=1.0)
    try:
        if _RESUME_FILE.exists():
            _RESUME_FILE.unlink()
    except Exception:
        pass
    try:
        if _PAUSE_FILE.exists():
            _PAUSE_FILE.unlink()
    except Exception:
        pass
    _log("mitm_addon done")

def _is_sendraw(item: Any) -> bool:
    return isinstance(item, dict) and item.get("method") == "eth_sendRawTransaction"


def _extract_meta(headers: Dict[str, str], item: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    meta = _parse_id_json(item.get("id"))
    if meta:
        return meta, "id"
    hdr = headers.get("X-EEST-ID") or headers.get("x-eest-id")
    meta = _parse_header_json(hdr)
    if meta:
        return meta, "header"
    return None, "none"


def _append_raw_request_line(path: pathlib.Path, obj: Any) -> None:
    try:
        _append_line(path, _minified_json_line(obj))
    except Exception as e:
        _log(f"append raw request failed ({path}): {e}")


def _record_sendraw(item: Dict[str, Any], headers: Dict[str, str]) -> None:
    global _ACTIVE_GRP, _LAST_SENDRAW_TS, _PENDING, _BUF, _TESTS_STARTED

    if not DISABLE_OVERLAY_RESTORE:
        _wait_for_resume()
        # NOTE: overlay restore is triggered exclusively by eth_getBalance for
        # OVERLAY_RESTORE_TRIGGER_ADDRESS in the request() hook — not here.

    params = item.get("params") or []
    raw = params[0] if params and isinstance(params[0], str) and params[0].startswith("0x") else None
    if not raw:
        _log("sendraw missing/invalid txrlp")
        return

    meta, src = _extract_meta(headers, item)

    scenario_name = "global-nophase"
    phase_name = "none"

    # Recognize global no-phase: metadata present but no 'phase'
    if meta and not (meta.get("phase")):
        grp = ("global-nophase", "global-nophase", "global-nophase")
        phase_name = "global-nophase"
        _log(f"intercept sendraw id={item.get('id')} grp={grp} via={src} (no-phase)")
    elif meta:
        grp = _derive_group_from_meta(meta)
        phase_name = meta.get("phase") or "unknown"
        try:
            scenario_name = _scenario_name(grp[0], grp[1])
        except Exception:
            scenario_name = "unknown"
        _log(f"intercept sendraw id={item.get('id')} grp={grp} via={src}")
        # If phase is literally "unknown" → also append raw request to unknown.txt
        if (meta.get("phase") or "").lower() == "unknown":
            _ensure_dirs_and_cleanup_old()
            _append_raw_request_line(_UNKNOWN_FILE, item)
        # Mark tests started on first phased test
        ph = (meta.get("phase") or "").lower()
        if ph in {"setup", "testing", "cleanup"}:
            if not _TESTS_STARTED:
                _TESTS_STARTED = True
                _log("tests lifecycle: STARTED")
    else:
        # Missing metadata → treat like global no-phase lifecycle (we'll route via setup/middle/teardown)
        grp = ("global-nophase", "global-nophase", "global-nophase")
        _log(f"intercept sendraw fallback→global-nophase id={item.get('id', 'noid')}")

    # Always log sendRawTransaction IDs with meta summary (to both mitm and merged logs)
    tx_index = (meta or {}).get("txIndex") if meta else None
    _log(
        f"[SENDRAW] id={item.get('id')} phase={phase_name} scenario={scenario_name} tx_index={tx_index} via={src}",
        to_merged=True,
    )

    current_extra_data_label = _extra_data_label_from_meta(meta)
    parsed_tx_index: Optional[int] = None
    if tx_index is not None:
        try:
            parsed_tx_index = int(tx_index)
        except (ValueError, TypeError):
            parsed_tx_index = None
    force_prev: Optional[Tuple[Tuple[str, str, str], List[Tuple[str, Any, Optional[str], Optional[int]]]]] = None
    with _GROUP_LOCK:
        if _ACTIVE_GRP and grp != _ACTIVE_GRP and _PENDING:
            force_prev = (_ACTIVE_GRP, list(_BUF))
            _PENDING = False
            _BUF = []
        _ACTIVE_GRP = grp
        _BUF.append((raw, item.get("id"), current_extra_data_label, parsed_tx_index))
        _PENDING = True
        _LAST_SENDRAW_TS = time.time()
    _log(f"buffered tx: group={grp} buf_size={len(_BUF)} tx_index={parsed_tx_index}")

    if force_prev:
        prev_grp, prev_buf = force_prev
        prev_buf.sort(key=lambda x: (x[3] is None, x[3] if x[3] is not None else 0))
        _log(f"group switch → force flush prev={prev_grp} size={len(prev_buf)}")
        prev_last_extra_data_label = prev_buf[-1][2] if prev_buf else None
        _flush_group(prev_grp, [x[0] for x in prev_buf], last_extra_data_label=prev_last_extra_data_label)


# ---- mitmproxy HTTP hooks --------------------------------------------------

def clientconnect(con) -> None:
    peer = getattr(con, "peername", None) or getattr(con, "address", None)
    _log(f"clientconnect peer={peer}")


def clientdisconnect(con) -> None:
    peer = getattr(con, "peername", None) or getattr(con, "address", None)
    _log(f"clientdisconnect peer={peer}")


def serverconnect(con) -> None:
    addr = getattr(con, "address", None)
    _log(f"serverconnect addr={addr}")


def serverdisconnect(con) -> None:
    addr = getattr(con, "address", None)
    _log(f"serverdisconnect addr={addr}")


def request(flow: http.HTTPFlow) -> None:
    start_time = perf_counter()
    if not DISABLE_OVERLAY_RESTORE:
        _wait_for_resume()

    body_text = ""
    try:
        hdrs = {k: str(v) for k, v in flow.request.headers.items()}
        start_parse = perf_counter()
        try:
            body_text = flow.request.get_text("utf-8")
        except Exception:
            body_text = (
                flow.request.content[:4096].decode("utf-8", errors="ignore") if flow.request.content else ""
            )
        if _should_log_verbose():
            _log(
                f"REQ POST {flow.request.host}:{flow.request.port}{flow.request.path} "
                f"headers={json.dumps(hdrs)} body_preview={body_text[:2048]}"
            )
    except Exception as e:
        _log(f"REQ log error: {e}")

    if flow.request.method.upper() != "POST":
        duration = perf_counter() - start_time
        if _should_log_verbose():
            _log(f"REQ non-POST handled in {duration:.4f}s")
        return

    try:
        req_obj = json.loads(body_text)
    except Exception:
        duration = perf_counter() - start_time
        _log(f"REQ parse error after {duration:.4f}s")
        return

    entries: List[Dict[str, Any]] = []
    if isinstance(req_obj, dict):
        entries = [req_obj]
    elif isinstance(req_obj, list):
        entries = [entry for entry in req_obj if isinstance(entry, dict)]

    if not DISABLE_OVERLAY_RESTORE:
        pending = globals().get("_PENDING_OVERLAY")
        if pending:
            # Trigger overlay restore ONLY when eth_getBalance for the designated
            # trigger address is observed (deterministic reorg point).
            is_trigger = False
            for entry in entries:
                method = entry.get("method") if isinstance(entry, dict) else None
                if method == "eth_getBalance":
                    params = entry.get("params", [])
                    addr = (params[0] if params and isinstance(params[0], str) else "").lower()
                    if addr == OVERLAY_RESTORE_TRIGGER_ADDRESS:
                        is_trigger = True
                        break
            if is_trigger:
                # Gate: wait until ALL testing-phase txs are confirmed first.
                with _PENDING_TX_LOCK:
                    remaining = len(_PENDING_TX_HASHES)
                if remaining > 0:
                    _log(
                        f"eth_getBalance trigger for {OVERLAY_RESTORE_TRIGGER_ADDRESS} but {remaining} "
                        f"tx(s) still unconfirmed; blocking until all confirmed"
                    )
                _wait_for_all_tx_confirmed()
                scenario, stage, block_hash = pending
                globals()["_PENDING_OVERLAY"] = None
                _clear_pending_tx_hashes()
                _log(
                    f"overlay restore triggered by eth_getBalance for "
                    f"{OVERLAY_RESTORE_TRIGGER_ADDRESS} scenario={scenario}"
                )
                _signal_cleanup_pause("__overlay_restore__", stage, block_hash)
                _wait_for_resume()
                _insert_empty_hook_separator("confirmed-restore-before-next-request", scenario)

    # Fast path: if tx lookup starts, flush pending buffered sendraws immediately.
    has_get_tx_by_hash = any(entry.get("method") == "eth_getTransactionByHash" for entry in entries)
    if has_get_tx_by_hash and _has_pending_buffered_sendraw():
        with _GROUP_LOCK:
            gap = time.time() - _LAST_SENDRAW_TS
            buf_size = len(_BUF)
        _log(
            f"eth_getTransactionByHash observed with pending sendraw buffer -> forcing flush "
            f"(gap_since_last_sendraw={gap:.3f}s buf_size={buf_size})"
        )
        _force_flush_buffer()

    if isinstance(req_obj, list):
        if not entries:
            return
        has_sendraw = any(_is_sendraw(it) for it in entries)
        if not has_sendraw:
            return

        local_results: List[Dict[str, Any]] = []
        forward_entries: List[Dict[str, Any]] = []
        for it in entries:
            if _is_sendraw(it):
                params = it.get("params") or []
                raw = params[0] if params and isinstance(params[0], str) else None
                if not raw:
                    local_results.append(_sendraw_error_response(it, "Invalid params: missing raw transaction"))
                    continue
                try:
                    tx_hash = _tx_hash_from_raw(raw)
                    _record_sendraw(it, flow.request.headers)
                    local_results.append(_sendraw_success_response(it, tx_hash))
                except Exception as e:
                    local_results.append(_sendraw_error_response(it, f"Failed to hash raw transaction: {e}"))
            else:
                forward_entries.append(it)

        upstream_results: List[Dict[str, Any]] = []
        if forward_entries:
            try:
                upstream_obj = _http_post_json(
                    RPC_DIRECT,
                    forward_entries,
                    timeout=30,
                    headers={"Content-Type": "application/json"},
                )
                if isinstance(upstream_obj, list):
                    upstream_results = [x for x in upstream_obj if isinstance(x, dict)]
                elif isinstance(upstream_obj, dict):
                    upstream_results = [upstream_obj]
            except Exception as e:
                _log(f"WARN mixed batch forward failed: {e}")
                for it in forward_entries:
                    upstream_results.append(_sendraw_error_response(it, f"Upstream forwarding failed: {e}"))

        merged: List[Dict[str, Any]] = []
        merged.extend(upstream_results)
        merged.extend(local_results)
        flow.response = http.Response.make(
            200,
            json.dumps(merged),
            {"Content-Type": "application/json"},
        )
        return

    if isinstance(req_obj, dict) and _is_sendraw(req_obj):
        params = req_obj.get("params") or []
        raw = params[0] if params and isinstance(params[0], str) else None
        if not raw:
            body = _sendraw_error_response(req_obj, "Invalid params: missing raw transaction")
        else:
            try:
                tx_hash = _tx_hash_from_raw(raw)
                _record_sendraw(req_obj, flow.request.headers)
                body = _sendraw_success_response(req_obj, tx_hash)
            except Exception as e:
                body = _sendraw_error_response(req_obj, f"Failed to hash raw transaction: {e}")
        flow.response = http.Response.make(
            200,
            json.dumps(body),
            {"Content-Type": "application/json"},
        )
        return


def response(flow: http.HTTPFlow) -> None:
    start_time = perf_counter()
    body_text = ""
    try:
        hdrs = {k: str(v) for k, v in flow.response.headers.items()}
        try:
            body_text = flow.response.get_text("utf-8")
        except Exception:
            body_text = (
                flow.response.content[:4096].decode("utf-8", errors="ignore") if flow.response.content else ""
            )
        if _should_log_verbose():
            _log(
                f"RESP POST {flow.request.host}:{flow.request.port}{flow.request.path} "
                f"status={flow.response.status_code} headers={json.dumps(hdrs)} "
                f"body_preview={body_text[:2048]}"
            )
    except Exception as e:
        _log(f"RESP log error: {e}")

    try:
        req_text = flow.request.get_text("utf-8")
    except Exception:
        req_text = (
            flow.request.content[:4096].decode("utf-8", errors="ignore") if flow.request.content else ""
        )

    if flow.request.method.upper() != "POST":
        duration = perf_counter() - start_time
        if _should_log_verbose():
            _log(f"RESP non-POST handled in {duration:.4f}s")
        return

    try:
        req_obj = json.loads(req_text) if req_text else None
    except Exception:
        req_obj = None

    pending = globals().get("_PENDING_OVERLAY")
    if not pending:
        return

    entries: list[Any] = []
    if isinstance(req_obj, dict):
        entries = [req_obj]
    elif isinstance(req_obj, list):
        entries = [entry for entry in req_obj if isinstance(entry, dict)]
    else:
        return

    pending_scenario, pending_stage, pending_block = pending

    try:
        resp_obj = json.loads(body_text) if body_text else None
    except Exception:
        resp_obj = None

    # Build response lookup by id for batch matching
    resp_by_id: Dict[Any, Dict] = {}
    if isinstance(resp_obj, dict):
        resp_by_id[resp_obj.get("id")] = resp_obj
    elif isinstance(resp_obj, list):
        for r in resp_obj:
            if isinstance(r, dict):
                resp_by_id[r.get("id")] = r

    for entry in entries:
        method = entry.get("method")
        if method != "eth_getTransactionByHash":
            continue

        meta, _ = _extract_meta(flow.request.headers, entry)
        scenario = None
        if meta:
            try:
                grp = _derive_group_from_meta(meta)
                scenario = _scenario_name(grp[0], grp[1])
            except Exception:
                scenario = None
        if scenario is None:
            scenario = pending_scenario
        if scenario != pending_scenario:
            continue

        matched_resp = resp_by_id.get(entry.get("id"))
        if matched_resp is None and isinstance(resp_obj, dict):
            matched_resp = resp_obj  # fallback for single responses
        if not isinstance(matched_resp, dict):
            continue
        if matched_resp.get("result") in (None, False):
            continue

        params = entry.get("params", [])
        queried_hash = (params[0] if params and isinstance(params[0], str) else "").lower()
        with _PENDING_TX_LOCK:
            removed = queried_hash in _PENDING_TX_HASHES
            _PENDING_TX_HASHES.discard(queried_hash)
            remaining = len(_PENDING_TX_HASHES)
            if not _PENDING_TX_HASHES:
                _ALL_TX_CONFIRMED_EVENT.set()
        if removed:
            _log(f"tx confirmed: {queried_hash[:18]}... remaining={remaining} scenario={scenario}")
        else:
            _log(f"tx confirmation observed (not in pending set): {queried_hash[:18]}... scenario={scenario}")

