"""
mitmproxy addon to capture and hash Engine API request and/or response bodies.

This addon intercepts Engine API traffic and:
1. Captures hashes for engine_newPayloadV4 and engine_forkchoiceUpdatedV3 methods
2. Optionally logs ALL JSON-RPC methods' full request/response bodies to YAML

Hashes are mapped to test names via a temp file written by run.sh before each test.

Hash capture modes:
- "request": Hash only request bodies (default, backward compatible)
- "response": Hash only response bodies
- "all": Hash both request and response bodies

Logging:
- When log_dir is provided, ALL JSON-RPC methods are logged (not just hash-captured ones)
- Logs are saved as structured YAML with literal block style for readability

Configuration is passed via HASH_CAPTURE_CONFIG environment variable as JSON:
{
    "client": "nethermind",
    "run": 1,
    "output_dir": "response_hashes",
    "mode": "all",  # optional, defaults to "request"
    "log_dir": "mitmproxy_logs"  # optional, enables full request/response logging
}

Usage:
    mitmdump -p 8552 --mode reverse:http://127.0.0.1:8551 -s hash_capture_addon.py
"""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import yaml
from mitmproxy import http


class _LiteralStr(str):
    """String subclass to mark strings for literal block style in YAML."""
    pass


def _literal_str_representer(dumper, data):
    """Representer that outputs strings with literal block style (|)."""
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')


class _LiteralDumper(yaml.SafeDumper):
    """Custom YAML dumper that uses literal block style for marked strings."""
    pass


_LiteralDumper.add_representer(_LiteralStr, _literal_str_representer)


# Methods to capture
METHODS_TO_CAPTURE = {"engine_newPayloadV4", "engine_forkchoiceUpdatedV3"}

# Valid capture modes
VALID_MODES = {"request", "response", "all"}

# Temp file where run.sh writes the current test name
CURRENT_TEST_FILE = "/tmp/current_test_name.txt"


def normalize(value):
    """
    Recursively normalize a JSON value for consistent hashing.
    Same logic as hash_json_file() in run.sh.
    """
    if isinstance(value, dict):
        return {k: normalize(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def hash_response(response_body: bytes) -> str:
    """
    Hash a JSON response body using SHA256 with normalization.
    """
    try:
        data = json.loads(response_body.decode("utf-8"))
        normalized = normalize(data)
        payload = json.dumps(normalized, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
    except (json.JSONDecodeError, UnicodeDecodeError):
        # If we can't parse as JSON, hash the raw bytes
        return hashlib.sha256(response_body).hexdigest()


def get_current_test_name() -> str:
    """
    Read the current test name from the temp file.
    Returns empty string if file doesn't exist or is empty.
    """
    try:
        path = Path(CURRENT_TEST_FILE)
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            return content
    except Exception:
        pass
    return ""


def extract_method_from_request(request_body: bytes) -> str:
    """
    Extract the JSON-RPC method from the request body.
    """
    try:
        data = json.loads(request_body.decode("utf-8"))
        return data.get("method", "")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""


class HashCaptureAddon:
    """
    mitmproxy addon to capture and hash Engine API requests and/or responses.
    """

    def __init__(self):
        self.config = self._load_config()
        self.mode = self._get_mode()
        self.hashes = {
            "client": self.config.get("client", "unknown"),
            "run": self.config.get("run", 1),
            "mode": self.mode,
            "tests": {}
        }
        self.output_file = self._get_output_file()
        self.log_file = self._get_log_file()
        self.logs = {
            "client": self.config.get("client", "unknown"),
            "run": self.config.get("run", 1),
            "tests": {}
        }
        self.pending_requests = {}  # flow.id -> {"method": str, "request_hash": str | None, "request_body": bytes | None}

        # Load existing data if files exist (to resume/append)
        self._load_existing_hashes()
        self._load_existing_logs()
        print(f"[hash_capture] Mode: {self.mode}")
        if self.log_file:
            print(f"[hash_capture] Logging to: {self.log_file}")

    def _get_mode(self) -> str:
        """Get the capture mode from config, defaulting to 'request'."""
        mode = self.config.get("mode", "request")
        if mode not in VALID_MODES:
            print(f"[hash_capture] Warning: Invalid mode '{mode}', using 'request'")
            return "request"
        return mode

    def _load_config(self) -> dict:
        """Load configuration from HASH_CAPTURE_CONFIG environment variable."""
        config_str = os.environ.get("HASH_CAPTURE_CONFIG", "{}")
        try:
            return json.loads(config_str)
        except json.JSONDecodeError:
            print(f"[hash_capture] Warning: Invalid JSON in HASH_CAPTURE_CONFIG: {config_str}")
            return {}

    def _get_output_file(self) -> Path:
        """Get the output file path for hash results."""
        output_dir = Path(self.config.get("output_dir", "response_hashes"))
        output_dir.mkdir(parents=True, exist_ok=True)

        client = self.config.get("client", "unknown")
        run = self.config.get("run", 1)
        return output_dir / f"{client}_run_{run}.json"

    def _get_log_file(self) -> Path | None:
        """Get the log file path for request/response logging, or None if disabled."""
        log_dir = self.config.get("log_dir")
        if not log_dir:
            return None

        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        client = self.config.get("client", "unknown")
        run = self.config.get("run", 1)
        return log_path / f"{client}_run_{run}.yaml"

    def _load_existing_logs(self):
        """Load existing logs from YAML file if it exists."""
        if not self.log_file or not self.log_file.exists():
            return
        try:
            with self.log_file.open("r", encoding="utf-8") as f:
                existing = yaml.safe_load(f)
                if existing and "tests" in existing:
                    self.logs["tests"] = existing["tests"]
                    print(f"[hash_capture] Loaded {len(self.logs['tests'])} existing log entries")
        except (yaml.YAMLError, IOError) as e:
            print(f"[hash_capture] Warning: Could not load existing logs: {e}")

    def _save_logs(self):
        """Save logs to the YAML file with literal block style for multi-line strings."""
        if not self.log_file:
            return
        try:
            with self.log_file.open("w", encoding="utf-8") as f:
                yaml.dump(self.logs, f, default_flow_style=False, allow_unicode=True,
                          sort_keys=False, default_style=None, Dumper=_LiteralDumper)
        except IOError as e:
            print(f"[hash_capture] Error saving logs: {e}")

    def _format_body(self, body: bytes | None) -> _LiteralStr:
        """Format a request/response body as pretty JSON string with literal block style."""
        if not body:
            return _LiteralStr("")
        try:
            data = json.loads(body.decode("utf-8"))
            return _LiteralStr(json.dumps(data, indent=2))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _LiteralStr(body.decode("utf-8", errors="replace"))

    def _log_entry(self, test_name: str, method: str, request_body: bytes | None, response_body: bytes | None):
        """Log a request/response pair to the structured YAML logs."""
        if not self.log_file:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        # Initialize test entry if needed
        if test_name not in self.logs["tests"]:
            self.logs["tests"][test_name] = {}

        # Store the log entry
        self.logs["tests"][test_name][method] = {
            "timestamp": timestamp,
            "request": self._format_body(request_body),
            "response": self._format_body(response_body)
        }

        # Save after each update
        self._save_logs()

    def _load_existing_hashes(self):
        """Load existing hashes from output file if it exists."""
        if self.output_file.exists():
            try:
                with self.output_file.open("r", encoding="utf-8") as f:
                    existing = json.load(f)
                    if "tests" in existing:
                        self.hashes["tests"] = existing["tests"]
                        print(f"[hash_capture] Loaded {len(self.hashes['tests'])} existing test entries")
            except (json.JSONDecodeError, IOError) as e:
                print(f"[hash_capture] Warning: Could not load existing hashes: {e}")

    def _save_hashes(self):
        """Save hashes to the output file."""
        try:
            with self.output_file.open("w", encoding="utf-8") as f:
                json.dump(self.hashes, f, indent=2)
        except IOError as e:
            print(f"[hash_capture] Error saving hashes: {e}")

    def request(self, flow: http.HTTPFlow):
        """
        Intercept requests to hash the request body and track the method.
        """
        if flow.request.content:
            method = extract_method_from_request(flow.request.content)
            if not method:
                return

            # Determine if we should track this request
            should_hash = method in METHODS_TO_CAPTURE
            should_log = self.log_file is not None

            if should_hash or should_log:
                # Calculate request hash if mode requires it and method is hashable
                request_hash = None
                if should_hash and self.mode in ("request", "all"):
                    request_hash = hash_response(flow.request.content)
                self.pending_requests[flow.id] = {
                    "method": method,
                    "request_hash": request_hash,
                    "request_body": flow.request.content if should_log else None,
                    "should_hash": should_hash
                }

    def response(self, flow: http.HTTPFlow):
        """
        Intercept responses to store captured hashes and/or log request/response.
        """
        # Check if this was a request we're tracking
        pending = self.pending_requests.pop(flow.id, None)
        if not pending:
            return

        method = pending["method"]
        request_hash = pending["request_hash"]
        request_body = pending.get("request_body")
        should_hash = pending.get("should_hash", False)

        # Get the current test name
        test_name = get_current_test_name()
        if not test_name:
            print(f"[hash_capture] Warning: No test name found for {method}")
            return

        # Calculate response hash if mode requires it and method is hashable
        response_hash = None
        response_body = None
        if flow.response and flow.response.content:
            if should_hash and self.mode in ("response", "all"):
                response_hash = hash_response(flow.response.content)
            if self.log_file:
                response_body = flow.response.content

        # Log request/response if logging is enabled (for ALL methods)
        if self.log_file:
            self._log_entry(test_name, method, request_body, response_body)
            print(f"[hash_capture] Logged {method} for '{test_name}'")

        # Store hashes only for specific methods
        if should_hash:
            if test_name not in self.hashes["tests"]:
                self.hashes["tests"][test_name] = {}

            now = datetime.now().strftime("%H:%M")
            # Store based on mode
            if self.mode == "request":
                # Backward compatible: flat hash string
                self.hashes["tests"][test_name][method] = request_hash
                print(f"[hash_capture] [{now}] Captured {method} request for '{test_name}': {request_hash[:16]}...")
            elif self.mode == "response":
                # Flat hash string for response only
                self.hashes["tests"][test_name][method] = response_hash
                print(f"[hash_capture] [{now}] Captured {method} response for '{test_name}': {response_hash[:16]}...")
            else:  # mode == "all"
                # Nested structure with both hashes
                self.hashes["tests"][test_name][method] = {
                    "request": request_hash,
                    "response": response_hash
                }
                print(f"[hash_capture] [{now}] Captured {method} for '{test_name}': req={request_hash[:16]}... resp={response_hash[:16]}...")

            # Save after each update
            self._save_hashes()


# Create the addon instance
addons = [HashCaptureAddon()]
