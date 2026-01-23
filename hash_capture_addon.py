"""
mitmproxy addon to capture and hash Engine API request bodies.

This addon intercepts Engine API traffic and captures request body hashes for
engine_newPayloadV4 and engine_forkchoiceUpdatedV3 methods. Hashes are
mapped to test names via a temp file written by run.sh before each test.

Note: We hash REQUEST bodies (not responses) because Engine API responses are
just status acknowledgments with the same format for all tests. The request
bodies contain the actual payload data (blocks, transactions, state roots)
which differs per test.

Configuration is passed via HASH_CAPTURE_CONFIG environment variable as JSON:
{
    "client": "nethermind",
    "run": 1,
    "output_dir": "response_hashes"
}

Usage:
    mitmdump -p 8552 --mode reverse:http://127.0.0.1:8551 -s hash_capture_addon.py
"""

import hashlib
import json
import os
from pathlib import Path
from mitmproxy import http


# Methods to capture
METHODS_TO_CAPTURE = {"engine_newPayloadV4", "engine_forkchoiceUpdatedV3"}

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
    mitmproxy addon to capture and hash Engine API responses.
    """

    def __init__(self):
        self.config = self._load_config()
        self.hashes = {
            "client": self.config.get("client", "unknown"),
            "run": self.config.get("run", 1),
            "tests": {}
        }
        self.output_file = self._get_output_file()
        self.pending_requests = {}  # flow.id -> {"method": str, "request_hash": str}

        # Load existing hashes if file exists (to resume/append)
        self._load_existing_hashes()

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
            if method in METHODS_TO_CAPTURE:
                request_hash = hash_response(flow.request.content)
                self.pending_requests[flow.id] = {
                    "method": method,
                    "request_hash": request_hash
                }

    def response(self, flow: http.HTTPFlow):
        """
        Intercept responses to store the request hash captured earlier.
        """
        # Check if this was a request we're tracking
        pending = self.pending_requests.pop(flow.id, None)
        if not pending:
            return

        method = pending["method"]
        request_hash = pending["request_hash"]

        # Get the current test name
        test_name = get_current_test_name()
        if not test_name:
            print(f"[hash_capture] Warning: No test name found for {method}")
            return

        # Store the request hash
        if test_name not in self.hashes["tests"]:
            self.hashes["tests"][test_name] = {}

        self.hashes["tests"][test_name][method] = request_hash

        # Save after each update
        self._save_hashes()

        print(f"[hash_capture] Captured {method} request for '{test_name}': {request_hash[:16]}...")


# Create the addon instance
addons = [HashCaptureAddon()]
