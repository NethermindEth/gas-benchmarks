"""
mitm_addon.py — formatted and structured

This mitmproxy addon intercepts JSON-RPC POST requests and buffers
`eth_sendRawTransaction` calls into groups derived from test metadata.
When a quiet period elapses (or groups switch), it requests a payload via
`engine_getPayloadV4` and submits it via `engine_newPayloadV4`, also
updating forkchoice. It logs activity to /root/mitm_addon.log and
optionally anchors finalized/safe blocks from a dynamic global setup.

Environment:
  MITM_ADDON_CONFIG: path to JSON config (default: mitm_config.json)
    {
      "rpc_direct": "http://...",
      "engine_url": "http://...",
      "jwt_hex_path": "/path/to/jwt.hex",
      "finalized_block": "0x..."     # optional
    }
"""
from __future__ import annotations

import base64
import hmac
import hashlib
import json
import os
import pathlib
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
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

_DYN_FINALIZED: str = FINALIZED_BLOCK

_u = urlparse(ENGINE_URL)
_ENGINE_HOST = _u.hostname or "127.0.0.1"
_ENGINE_PORT = _u.port or (443 if (_u.scheme == "https") else 80)
_ENGINE_PATH = _u.path or "/"

_LOG_FILE = "/root/mitm_logs.log"

# Quiet period before producing a block (seconds)
QUIET_SECONDS: float = 0.5

# Synchronization / state
_GROUP_LOCK = threading.Lock()
_ACTIVE_GRP: Optional[Tuple[str, str, str]] = None
_LAST_TS: float = 0.0
_PENDING: bool = False
_STAGE: Dict[Tuple[str, str, str], int] = {}
_BUF: List[Tuple[str, Any]] = []  # list of (txrlp_hex, original_id)
_STOP: bool = False

# Track tests that already had their first (setup) getPayload reorged
_TEST_REORGED: set[Tuple[str, str]] = set()  # {(file_base, test_name)}

# Thread handle
_MON_THR: Optional[threading.Thread] = None


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    """Append a log line to the log file; swallow all errors."""
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _http_post_json(url: str, obj: Any, timeout: int = 90, headers: Optional[Dict[str, str]] = None) -> Any:
    """POST JSON using requests if available, otherwise urllib."""
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (controlled URL)
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

    # Log with redacted Authorization
    eng_hdrs = {"Content-Type": "application/json", "Authorization": "Bearer <redacted>"}
    _log(
        f"REQ POST {_ENGINE_HOST}:{_ENGINE_PORT}{_ENGINE_PATH} "
        f"headers={json.dumps(eng_hdrs)} body_preview={json.dumps(body)}"
    )

    j = _http_post_json(ENGINE_URL, body, timeout=90, headers={"Authorization": f"Bearer {token}"})
    try:
        _log(
            f"RESP POST {_ENGINE_HOST}:{_ENGINE_PORT}{_ENGINE_PATH} "
            f"status=200 headers={json.dumps({'Content-Type':'application/json'})} "
            f"body_preview={json.dumps(j)}"
        )
    except Exception:
        _log(f"RESP POST {_ENGINE_HOST}:{_ENGINE_PORT}{_ENGINE_PATH} status=200 <non-json>")

    if "error" in j:
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
    except Exception as e:  # pragma: no cover - best effort
        _log(f"rpc {method} failed: {e}")
        return None


def _sanitize(s: Any) -> str:
    s = str(s).strip().strip("/\\").replace("/", "_").replace("\\", "_").replace("..", ".")
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

    file_base = _sanitize(os.path.basename(file_path_str))
    test_name = _sanitize(test_name)
    phase = _sanitize((meta or {}).get("phase") or "unknown")
    return (file_base, test_name, phase)


def _save_newpayload(exec_payload: Dict[str, Any], parent_hash: str, out_path: pathlib.Path) -> None:
    body = {
        "jsonrpc": "2.0",
        "id": int(time.time()),
        "method": "engine_newPayloadV4",
        "params": [exec_payload, [], parent_hash, []],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(body, f, indent=2)
        f.write("\n")
    tmp.replace(out_path)
    _log(f"saved {out_path}")


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


essential = ("pending", "queued")


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
# Flushing / Production
# ---------------------------------------------------------------------------

def _flush_group(grp: Tuple[str, str, str] | None, txrlps: List[str]) -> None:
    if not txrlps:
        _log(f"flush skipped: empty buffer for group={grp}")
        return

    try:
        _log_txpool_summary()
        first = txrlps[0]
        last = txrlps[-1]
        preview_first = (first[:18] + "…" + first[-10:]) if isinstance(first, str) else str(first)[:32]
        preview_last = (last[:18] + "…" + last[-10:]) if isinstance(last, str) else str(last)[:32]

        file_base, test_name, phase = grp or ("unknown", "unknown", "unknown")

        # Reorg completely disabled
        reorg = False

        _log(
            f"GETPAYLOAD group={grp} count={len(txrlps)} first={preview_first} "
            f"last={preview_last} reorg={str(reorg).lower()}"
        )

        params: List[Any] = [txrlps, "EMPTY"]
        # reorg flag never appended

        payload: Dict[str, Any] = _engine("engine_getPayloadV4", params)
        exec_payload: Dict[str, Any] = payload.get("executionPayload", {})
        parent_hash: str = exec_payload.get("parentHash") or "0x" + ("00" * 32)

        if grp not in _STAGE:
            _STAGE[grp] = 0
        _STAGE[grp] += 1
        idx = _STAGE[grp]

        out_path = pathlib.Path("payloads") / file_base / test_name / phase / (str(idx) + ".json")
        _save_newpayload(exec_payload, parent_hash, out_path)

        _engine("engine_newPayloadV4", [exec_payload, [], parent_hash, []])

        if file_base == "global-setup":
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
        _engine("engine_forkchoiceUpdatedV3", [fcs, None])
        _log(f"produced block group={grp} stage={idx}")
    except Exception as e:  # pragma: no cover - production safety
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
        time.sleep(0.2)


# ---------------------------------------------------------------------------
# mitmproxy addon hooks
# ---------------------------------------------------------------------------

def load(loader) -> None:  # noqa: D401 - mitmproxy hook
    """Start the background monitor thread when the addon loads."""
    global _MON_THR
    _MON_THR = threading.Thread(target=_monitor, daemon=True)
    _MON_THR.start()
    _log("mitm_addon loaded")


def done() -> None:  # noqa: D401 - mitmproxy hook
    """Stop the background thread on shutdown."""
    global _STOP
    _STOP = True
    if _MON_THR and _MON_THR.is_alive():
        _MON_THR.join(timeout=1.0)
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


def _record_sendraw(item: Dict[str, Any], headers: Dict[str, str]) -> None:
    global _ACTIVE_GRP, _LAST_TS, _PENDING, _BUF

    params = item.get("params") or []
    raw = params[0] if params and isinstance(params[0], str) and params[0].startswith("0x") else None
    if not raw:
        _log("sendraw missing/invalid txrlp")
        return

    meta, src = _extract_meta(headers, item)
    if meta:
        grp = _derive_group_from_meta(meta)
        _log(f"intercept sendraw id={item.get('id')} grp={grp} via={src}")
    else:
        txid = str(item.get("id", "noid"))
        grp = ("global-setup", "global-setup", "global-setup")
        out = pathlib.Path("payloads") / "global-setup" / f"{txid}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            with out.open("w", encoding="utf-8") as f:
                json.dump(item, f, indent=2)
                f.write("\n")
        except Exception:
            pass
        _log(f"intercept sendraw fallback→global-setup id={txid}")

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
    try:
        hdrs = {k: str(v) for k, v in flow.request.headers.items()}
        try:
            body_text = flow.request.get_text("utf-8")
        except Exception:
            body_text = (
                flow.request.content[:4096].decode("utf-8", errors="ignore") if flow.request.content else ""
            )
        _log(
            f"REQ POST {flow.request.host}:{flow.request.port}{flow.request.path} "
            f"headers={json.dumps(hdrs)} body_preview={body_text[:2048]}"
        )
    except Exception as e:
        _log(f"REQ log error: {e}")

    if flow.request.method.upper() != "POST":
        return

    try:
        req_obj = json.loads(body_text)
    except Exception:
        return

    if isinstance(req_obj, list):
        for it in req_obj:
            if _is_sendraw(it):
                _record_sendraw(it, flow.request.headers)
        return

    if isinstance(req_obj, dict) and _is_sendraw(req_obj):
        _record_sendraw(req_obj, flow.request.headers)
        return


def response(flow: http.HTTPFlow) -> None:
    try:
        hdrs = {k: str(v) for k, v in flow.response.headers.items()}
        try:
            body_text = flow.response.get_text("utf-8")
        except Exception:
            body_text = (
                flow.response.content[:4096].decode("utf-8", errors="ignore") if flow.response.content else ""
            )
        _log(
            f"RESP POST {flow.request.host}:{flow.request.port}{flow.request.path} "
            f"status={flow.response.status_code} headers={json.dumps(hdrs)} "
            f"body_preview={body_text[:2048]}"
        )
    except Exception as e:
        _log(f"RESP log error: {e}")
