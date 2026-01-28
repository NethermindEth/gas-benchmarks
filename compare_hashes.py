#!/usr/bin/env python3
"""
Compare Engine API hashes across multiple clients.

This script takes multiple JSON hash files (output from hash_capture_addon.py)
and reports any mismatches or missing tests between clients.

Supports different comparison modes:
- request: Compare only request hashes (default, backward compatible)
- response: Compare only response hashes
- all: Compare both request and response hashes

Usage:
    python3 compare_hashes.py response_hashes/*.json
    python3 compare_hashes.py --mode request response_hashes/*.json
    python3 compare_hashes.py --mode response response_hashes/*.json
    python3 compare_hashes.py --mode all response_hashes/*.json
    python3 compare_hashes.py --mode all -j report.json response_hashes/*.json

Exit codes:
    0 - All hashes match across all clients
    1 - Mismatches found or errors occurred
"""

import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Set, Tuple, Any


def load_hash_file(path: Path) -> dict:
    """Load a hash file and return its contents."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def extract_hash(hash_value, hash_type: str) -> str | None:
    """
    Extract a specific hash from a hash value.

    Args:
        hash_value: Either a string (flat hash) or dict with "request"/"response" keys
        hash_type: One of "request", "response"

    Returns:
        The hash string or None if not available
    """
    if hash_value is None:
        return None
    if isinstance(hash_value, str):
        # Flat hash - assume it matches the requested type for backward compatibility
        return hash_value
    if isinstance(hash_value, dict):
        return hash_value.get(hash_type)
    return None


def compare_hashes(hash_files: List[Path], mode: str = "request") -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    Compare hashes across multiple files.

    Args:
        hash_files: List of hash file paths to compare
        mode: Comparison mode - "request", "response", or "all"

    Returns:
        Tuple of (all_match: bool, messages: List[str], json_report: dict)
    """
    messages = []
    all_match = True
    json_report: Dict[str, Any] = {
        "mode": mode,
        "clients": [],
        "total_tests": 0,
        "total_mismatches": 0,
        "result": "PASS",
        "mismatches": [],
        "missing_tests": {}
    }

    if len(hash_files) < 2:
        messages.append("Need at least 2 hash files to compare")
        json_report["result"] = "ERROR"
        return False, messages, json_report

    messages.append(f"Comparison mode: {mode}")

    # Load all hash files
    client_data = {}
    for path in hash_files:
        try:
            data = load_hash_file(path)
            client = data.get("client", path.stem)
            run = data.get("run", 1)
            file_mode = data.get("mode", "request")
            key = f"{client}_run_{run}"
            client_data[key] = data
            json_report["clients"].append(key)
            messages.append(f"Loaded {path}: {client} run {run} with {len(data.get('tests', {}))} tests (mode: {file_mode})")
        except (json.JSONDecodeError, IOError) as e:
            messages.append(f"Error loading {path}: {e}")
            all_match = False
            continue

    if len(client_data) < 2:
        messages.append("Need at least 2 valid hash files to compare")
        json_report["result"] = "ERROR"
        return False, messages, json_report

    # Collect all test names and methods across all clients
    all_tests: Set[str] = set()
    all_methods: Set[str] = set()

    for data in client_data.values():
        tests = data.get("tests", {})
        all_tests.update(tests.keys())
        for test_hashes in tests.values():
            all_methods.update(test_hashes.keys())

    json_report["total_tests"] = len(all_tests)
    messages.append(f"\nFound {len(all_tests)} unique tests across {len(client_data)} clients")
    messages.append(f"Methods tracked: {', '.join(sorted(all_methods))}")

    # Check for missing tests per client
    messages.append("\n--- Missing Tests ---")
    missing_found = False
    for client_key, data in client_data.items():
        tests = data.get("tests", {})
        client_tests = set(tests.keys())
        missing = all_tests - client_tests
        if missing:
            missing_found = True
            json_report["missing_tests"][client_key] = sorted(missing)
            messages.append(f"\n{client_key} is missing {len(missing)} tests:")
            for test in sorted(missing)[:10]:  # Show first 10
                messages.append(f"  - {test}")
            if len(missing) > 10:
                messages.append(f"  ... and {len(missing) - 10} more")

    if not missing_found:
        messages.append("No missing tests")

    # Determine which hash types to compare based on mode
    hash_types_to_compare = []
    if mode == "request":
        hash_types_to_compare = ["request"]
    elif mode == "response":
        hash_types_to_compare = ["response"]
    else:  # mode == "all"
        hash_types_to_compare = ["request", "response"]

    # Compare hashes for each test
    messages.append("\n--- Hash Comparison ---")
    mismatches = []

    for test_name in sorted(all_tests):
        for method in sorted(all_methods):
            for hash_type in hash_types_to_compare:
                # Collect hashes for this test/method/type from all clients
                hashes_by_client: Dict[str, str] = {}

                for client_key, data in client_data.items():
                    tests = data.get("tests", {})
                    if test_name in tests and method in tests[test_name]:
                        hash_value = tests[test_name][method]
                        extracted = extract_hash(hash_value, hash_type)
                        if extracted is not None:
                            hashes_by_client[client_key] = extracted

                # Skip if fewer than 2 clients have this hash
                if len(hashes_by_client) < 2:
                    continue

                # Check if all hashes match
                unique_hashes = set(hashes_by_client.values())
                if len(unique_hashes) > 1:
                    mismatches.append((test_name, method, hash_type, hashes_by_client))
                    # Add to JSON report with full hashes
                    json_report["mismatches"].append({
                        "test": test_name,
                        "method": method,
                        "type": hash_type,
                        "hashes": hashes_by_client
                    })

    json_report["total_mismatches"] = len(mismatches)

    if mismatches:
        all_match = False
        json_report["result"] = "FAIL"
        messages.append(f"\nFound {len(mismatches)} mismatches:")
        for test_name, method, hash_type, hashes in mismatches[:20]:  # Show first 20
            messages.append(f"\n  Test: {test_name}")
            messages.append(f"  Method: {method}")
            if mode == "all":
                messages.append(f"  Type: {hash_type}")
            for client_key, hash_val in sorted(hashes.items()):
                messages.append(f"    {client_key}: {hash_val[:16]}...")
        if len(mismatches) > 20:
            messages.append(f"\n  ... and {len(mismatches) - 20} more mismatches")
    else:
        messages.append("\nAll hashes match across clients!")

    # Summary
    messages.append("\n--- Summary ---")
    messages.append(f"Comparison mode: {mode}")
    messages.append(f"Total tests compared: {len(all_tests)}")
    messages.append(f"Total mismatches: {len(mismatches)}")
    messages.append(f"Result: {'PASS' if all_match else 'FAIL'}")

    return all_match, messages, json_report


def main():
    parser = argparse.ArgumentParser(
        description="Compare Engine API hashes across clients"
    )
    parser.add_argument(
        "hash_files",
        nargs="+",
        type=Path,
        help="JSON hash files to compare"
    )
    parser.add_argument(
        "-m", "--mode",
        choices=["request", "response", "all"],
        default="request",
        help="Hash type to compare: request, response, or all (default: request)"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Only print mismatches and summary"
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        help="Write text results to file instead of stdout"
    )
    parser.add_argument(
        "-j", "--json",
        type=Path,
        dest="json_output",
        help="Write JSON report with full hashes to file"
    )

    args = parser.parse_args()

    # Verify files exist
    for path in args.hash_files:
        if not path.exists():
            print(f"Error: File not found: {path}", file=sys.stderr)
            sys.exit(1)

    all_match, messages, json_report = compare_hashes(args.hash_files, mode=args.mode)

    # Filter messages if quiet mode
    if args.quiet:
        messages = [m for m in messages if "mismatch" in m.lower() or "summary" in m.lower() or "result" in m.lower()]

    output = "\n".join(messages)

    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"Text results written to {args.output}")
    else:
        print(output)

    # Write JSON report if requested
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        with args.json_output.open("w", encoding="utf-8") as f:
            json.dump(json_report, f, indent=2)
        print(f"JSON report written to {args.json_output}")

    sys.exit(0 if all_match else 1)


if __name__ == "__main__":
    main()
