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

from mitmproxy import http

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
SKIP_CLEANUP: bool = bool(_CFG.get("skip_cleanup"))
REUSE_GLOBALS: bool = bool(_CFG.get("reuse_globals"))

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

_MERGED_LOG_RAW = _CFG.get("merged_log_path")
_MERGED_LOG_PATH = pathlib.Path(_MERGED_LOG_RAW).expanduser() if _MERGED_LOG_RAW else _LOG_FILE_PATH.with_name("mitm_nethermind.log")
if not _MERGED_LOG_PATH.is_absolute():
    _MERGED_LOG_PATH = _MERGED_LOG_PATH.resolve()

_NETHERMIND_CONTAINER = _CFG.get("nethermind_container") or "eest-nethermind"
_LIGHT_LOG = bool(_CFG.get("light_logs", True))
_LIGHT_PREFIX_KEEP = ("[MITM]", "[NM]", "ERROR", "WARN", "overlay", "PAUSE", "RESUME")
_NM_LAST_TS: Optional[str] = None

# Quiet period before producing a block (seconds)
QUIET_SECONDS: float = 0.1

# Synchronization / state
_GROUP_LOCK = threading.Lock()
_ACTIVE_GRP: Optional[Tuple[str, str, str]] = None  # (file_base, test_name, phase)
_LAST_TS: float = 0.0
_PENDING: bool = False
_STAGE: Dict[Tuple[str, str, str], int] = {}
_BUF: List[Tuple[str, Any]] = []  # list of (txrlp_hex, original_id)
_STOP: bool = False

# Thread handle
_MON_THR: Optional[threading.Thread] = None

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
_PENDING_OVERLAY_CONFIRMED: bool = False

_OVERLAY_PRIMED: bool = False


def _scenario_file_path(phase: str, scenario: str) -> pathlib.Path:
    base = _PHASE_BASE_DIRS.get(phase.lower())
    if base is None:
        raise ValueError(f"unknown phase '{phase}'")
    idx = _register_scenario(scenario)
    base.mkdir(parents=True, exist_ok=True)
    scenario_dir = base / f"{idx:06d}"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    return scenario_dir / f"{scenario}.txt"


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
        if _LIGHT_LOG and not to_merged:
            if not msg.startswith(_LIGHT_PREFIX_KEEP):
                return
        line = f"{_now_ts()} {msg}"
        _append_lines(_LOG_FILE_PATH, [line])
        if to_merged:
            _append_lines(_MERGED_LOG_PATH, [line])
    except Exception:
        pass


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
    summary = f"[MITM][NP] block={block_hash} parent={parent_hash} txs={len(txs)}"
    _log(summary, to_merged=True)

    nm_lines = _capture_nethermind_logs()
    if nm_lines:
        ts_prefix = _now_ts()
        prefixed = [f"{ts_prefix} [NM] {ln}" for ln in nm_lines]
        _append_lines(_MERGED_LOG_PATH, prefixed)
        _append_lines(_LOG_FILE_PATH, prefixed)


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

    if not _LIGHT_LOG:
        # Log with redacted Authorization
        eng_hdrs = {"Content-Type": "application/json", "Authorization": "Bearer <redacted>"}
        _log(
            f"REQ POST {_ENGINE_HOST}:{_ENGINE_PORT}{_ENGINE_PATH} "
            f"headers={json.dumps(eng_hdrs)} body_preview={json.dumps(body)}"
        )

    j = _http_post_json(ENGINE_URL, body, timeout=90, headers={"Authorization": f"Bearer {token}"})
    if not _LIGHT_LOG:
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


# ---------------------------------------------------------------------------
# File IO helpers for new layout
# ---------------------------------------------------------------------------

def _ensure_dirs_and_cleanup_old() -> None:
    _PAYLOADS_DIR.mkdir(parents=True, exist_ok=True)
    _SETUP_DIR.mkdir(parents=True, exist_ok=True)
    _TESTING_DIR.mkdir(parents=True, exist_ok=True)
    _CLEANUP_DIR.mkdir(parents=True, exist_ok=True)
    # Remove stray files directly under phase directories (legacy layout)
    for phase_dir in (_SETUP_DIR, _TESTING_DIR, _CLEANUP_DIR):
        try:
            for child in phase_dir.iterdir():
                if child.is_file() and child.suffix == ".txt":
                    try:
                        child.unlink()
                        _log(f"removed legacy top-level payload file: {child}")
                    except Exception as exc:
                        _log(f"failed to remove legacy payload file {child}: {exc}")
        except Exception:
            pass


def _minified_json_line(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))


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
    for phase in ("setup", "testing", "cleanup"):
        p = _scenario_file_path(phase, scenario)
        with p.open("w", encoding="utf-8"):
            pass
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

    if count == 0:
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

def _flush_group(grp: Tuple[str, str, str] | None, txrlps: List[str]) -> None:
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

        params: List[Any] = [txrlps, "EMPTY"]
        payload: Dict[str, Any] = _engine("engine_getPayloadV4", params)
        exec_payload: Dict[str, Any] = payload.get("executionPayload", {})
        parent_hash: str = exec_payload.get("parentHash") or "0x" + ("00" * 32)

        if grp not in _STAGE:
            _STAGE[grp] = 0
        _STAGE[grp] += 1
        idx = _STAGE[grp]

        np_body = {
            "jsonrpc": "2.0",
            "id": int(time.time()),
            "method": "engine_newPayloadV4",
            "params": [exec_payload, [], parent_hash, []],
        }

        _engine("engine_newPayloadV4", [exec_payload, [], parent_hash, []])
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
            if not _OVERLAY_PRIMED and not _TESTS_STARTED:
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

        _dump_pair_to_phase(ph, scenario, np_body, fcu_body)
        _log(f"produced block group={grp} stage={idx}")
        if ph == "cleanup":
            block_hash = exec_payload.get("blockHash")
            globals()['_PENDING_OVERLAY'] = (scenario, idx, block_hash)
            globals()['_PENDING_OVERLAY_CONFIRMED'] = False
            _log(f"cleanup stage {idx} complete for {scenario}; awaiting confirmation")
        elif SKIP_CLEANUP and ph == "testing":
            pending = globals().get('_PENDING_OVERLAY')
            if pending:
                pend_scenario, pend_stage, pend_block = pending
                if pend_scenario != scenario:
                    globals()['_PENDING_OVERLAY'] = None
                    _log(f"overlay restore pause triggered before scenario {scenario}")
                    _signal_cleanup_pause('__overlay_restore__', pend_stage, pend_block)
                    _wait_for_resume()
            globals()['_PENDING_OVERLAY'] = (scenario, idx, exec_payload.get("blockHash"))
            globals()['_PENDING_OVERLAY_CONFIRMED'] = False
            _log(f"testing stage {idx} complete for {scenario}; overlay refresh deferred to next scenario")
    except Exception as e:  # pragma: no cover
        _log(f"produce error: {e}")


def _produce_if_quiet(force: bool = False) -> None:
    global _PENDING, _BUF
    with _GROUP_LOCK:
        if not _PENDING and not force:
            return
        grp = _ACTIVE_GRP
        last = _LAST_TS
        age = time.time() - last
        if not force and age < QUIET_SECONDS:
            _log(f"waiting quiet: group={grp} age={age:.2f}s < {QUIET_SECONDS}s buf={len(_BUF)}")
            return

        buf_copy = list(_BUF)
        _PENDING = False
        _BUF = []
    _log(f"flushing group={grp} size={len(buf_copy)} reason={'force' if force else 'quiet'}")
    if buf_copy:
        _flush_group(grp, [x[0] for x in buf_copy])


def _monitor() -> None:
    while not _STOP:
        _produce_if_quiet(force=False)
        time.sleep(0.01)


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
                globals()['_PENDING_OVERLAY_CONFIRMED'] = False
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


def load(loader) -> None:
    global _MON_THR, _CONTROL_THREAD
    _ensure_dirs_and_cleanup_old()
    globals()['_OVERLAY_PRIMED'] = False
    globals()['_PENDING_OVERLAY'] = None
    globals()['_PENDING_OVERLAY_CONFIRMED'] = False
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
    _MON_THR = threading.Thread(target=_monitor, daemon=True)
    _MON_THR.start()
    _log('mitm_addon loaded')

def done() -> None:
    global _STOP, _CONTROL_THREAD
    # Remove any leftover empty .txt files (root and subdirs)
    _cleanup_empty_txt_files()

    _STOP = True
    _PAUSE_EVENT.set()
    if _MON_THR and _MON_THR.is_alive():
        _MON_THR.join(timeout=1.0)
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
    global _ACTIVE_GRP, _LAST_TS, _PENDING, _BUF, _TESTS_STARTED

    _wait_for_resume()

    pending_overlay = globals().get('_PENDING_OVERLAY')
    if pending_overlay:
        pending_scenario, pending_stage, pending_block = pending_overlay
        meta_probe, _src_probe = _extract_meta(headers, item)
        current_scenario = None
        if meta_probe:
            try:
                grp_probe = _derive_group_from_meta(meta_probe)
                current_scenario = _scenario_name(grp_probe[0], grp_probe[1])
            except Exception:
                current_scenario = None
        if current_scenario and current_scenario != pending_scenario:
            globals()['_PENDING_OVERLAY'] = None
            globals()['_PENDING_OVERLAY_CONFIRMED'] = False
            _log(f"overlay restore pause triggered before scenario {current_scenario}")
            _signal_cleanup_pause('__overlay_restore__', pending_stage, pending_block)
            _wait_for_resume()

    params = item.get("params") or []
    raw = params[0] if params and isinstance(params[0], str) and params[0].startswith("0x") else None
    if not raw:
        _log("sendraw missing/invalid txrlp")
        return

    meta, src = _extract_meta(headers, item)

    # Recognize global no-phase: metadata present but no 'phase'
    if meta and not (meta.get("phase")):
        grp = ("global-nophase", "global-nophase", "global-nophase")
        _log(f"intercept sendraw id={item.get('id')} grp={grp} via={src} (no-phase)")
    elif meta:
        grp = _derive_group_from_meta(meta)
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

    force_prev: Optional[Tuple[Tuple[str, str, str], List[Tuple[str, Any]]]] = None
    with _GROUP_LOCK:
        if _ACTIVE_GRP and grp != _ACTIVE_GRP and _PENDING:
            force_prev = (_ACTIVE_GRP, list(_BUF))
            _PENDING = False
            _BUF = []
        _ACTIVE_GRP = grp
        _BUF.append((raw, item.get("id")))
        _PENDING = True
        _LAST_TS = time.time()
    _log(f"buffered tx: group={grp} buf_size={len(_BUF)}")

    if force_prev:
        prev_grp, prev_buf = force_prev
        _log(f"group switch → force flush prev={prev_grp} size={len(prev_buf)}")
        _flush_group(prev_grp, [x[0] for x in prev_buf])


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
        if not _LIGHT_LOG:
            _log(
                f"REQ POST {flow.request.host}:{flow.request.port}{flow.request.path} "
                f"headers={json.dumps(hdrs)} body_preview={body_text[:2048]}"
            )
    except Exception as e:
        _log(f"REQ log error: {e}")

    if flow.request.method.upper() != "POST":
        duration = perf_counter() - start_time
        if not _LIGHT_LOG:
            _log(f"REQ non-POST handled in {duration:.4f}s")
        return

    try:
        req_obj = json.loads(body_text)
    except Exception:
        duration = perf_counter() - start_time
        _log(f"REQ parse error after {duration:.4f}s")
        return

    pending = globals().get("_PENDING_OVERLAY")
    pending_confirmed = globals().get("_PENDING_OVERLAY_CONFIRMED")
    if pending and pending_confirmed:
        entries: list[Any] = []
        if isinstance(req_obj, dict):
            entries = [req_obj]
        elif isinstance(req_obj, list):
            entries = [entry for entry in req_obj if isinstance(entry, dict)]
        else:
            entries = []
        trigger_method = None
        for entry in entries:
            method = entry.get("method") if isinstance(entry, dict) else None
            if method and method != "eth_getTransactionByHash":
                trigger_method = method
                break
        if trigger_method:
            scenario, stage, block_hash = pending
            globals()["_PENDING_OVERLAY"] = None
            globals()["_PENDING_OVERLAY_CONFIRMED"] = False
            _log(f"overlay restore pause triggered before method {trigger_method} for scenario {scenario}")
            _signal_cleanup_pause("__overlay_restore__", stage, block_hash)
            _wait_for_resume()

    if isinstance(req_obj, list):
        for it in req_obj:
            if _is_sendraw(it):
                _record_sendraw(it, flow.request.headers)
        return

    if isinstance(req_obj, dict) and _is_sendraw(req_obj):
        _record_sendraw(req_obj, flow.request.headers)
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
        if not _LIGHT_LOG:
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
        if not _LIGHT_LOG:
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

        if not isinstance(resp_obj, dict):
            continue
        if resp_obj.get("result") in (None, False):
            continue

        globals()["_PENDING_OVERLAY_CONFIRMED"] = True
        _log(f"cleanup confirmation observed for scenario {scenario}")
        return

