#!/usr/bin/env python3
"""Create warmup payload variants with unique prevRandao values and fix
blockHash mismatches from client validation errors.

Used by run.sh when OPCODES_WARMUP_COUNT > 1 to force re-execution of
warmup payloads that would otherwise be cached by blockHash.

Usage:
    python3 vary_warmup.py create  <input> <iteration> <output>
    python3 vary_warmup.py fix-hashes <variant_file> <response_dir> <client>
"""
import argparse
import json
import re
import sys
from pathlib import Path

_MISMATCH_RE = re.compile(
    r"Invalid block hash\s+(0x[0-9a-fA-F]{64})\s+does not match calculated hash\s+(0x[0-9a-fA-F]{64})",
    re.IGNORECASE,
)


def create_variant(input_path, iteration, output_path):
    """Create a copy of the warmup payload with a unique prevRandao."""
    new_prev_randao = f"0x{iteration:064x}"
    with open(input_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    with open(output_path, "w", encoding="utf-8") as f:
        for line in lines:
            stripped = line.strip()
            if not stripped:
                f.write(line)
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                f.write(line)
                continue
            method = obj.get("method", "")
            if "engine_newPayload" in method:
                params = obj.get("params")
                if isinstance(params, list) and params and isinstance(params[0], dict):
                    params[0]["prevRandao"] = new_prev_randao
            f.write(json.dumps(obj) + "\n")


def _extract_validation_errors(obj):
    """Recursively extract validation error strings from a JSON-RPC response."""
    errors = []
    if isinstance(obj, dict):
        for key in ("validationError", "validation_error"):
            val = obj.get(key)
            if isinstance(val, str) and val:
                errors.append(val)
        for nested in obj.values():
            errors.extend(_extract_validation_errors(nested))
    elif isinstance(obj, list):
        for item in obj:
            errors.extend(_extract_validation_errors(item))
    return errors


def fix_hashes(variant_path, response_dir, client):
    """Parse kute response files for blockHash mismatches and patch the variant."""
    response_path = Path(response_dir)
    mapping = {}

    for resp_file in sorted(response_path.glob(f"{client}_response_*.txt")):
        try:
            raw = resp_file.read_text(encoding="utf-8")
        except Exception:
            continue

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue

            for error in _extract_validation_errors(payload):
                mo = _MISMATCH_RE.search(error)
                if mo:
                    got = mo.group(1).lower()
                    want = mo.group(2).lower()
                    mapping[got] = want

    if not mapping:
        print("[WARN] No blockHash mismatches found in probe responses", file=sys.stderr)
        return False

    with open(variant_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    patched = 0
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            new_lines.append(line)
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            new_lines.append(line)
            continue

        params = obj.get("params")
        if isinstance(params, list) and params and isinstance(params[0], dict):
            current_hash = params[0].get("blockHash", "")
            if isinstance(current_hash, str):
                replacement = mapping.get(current_hash.lower())
                if replacement:
                    params[0]["blockHash"] = replacement
                    patched += 1

        new_lines.append(json.dumps(obj) + "\n")

    with open(variant_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"[INFO] Patched {patched} blockHash(es) in warmup variant", file=sys.stderr)
    return patched > 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")

    create_p = sub.add_parser("create", help="Create variant with unique prevRandao")
    create_p.add_argument("input", help="Original warmup file")
    create_p.add_argument("iteration", type=int, help="Iteration number (>= 2)")
    create_p.add_argument("output", help="Output variant file path")

    fix_p = sub.add_parser("fix-hashes", help="Patch blockHash from probe response errors")
    fix_p.add_argument("variant", help="Variant file to patch")
    fix_p.add_argument("response_dir", help="Directory with kute response files")
    fix_p.add_argument("client", help="Client name for response file matching")

    args = parser.parse_args()

    if args.command == "create":
        create_variant(args.input, args.iteration, args.output)
    elif args.command == "fix-hashes":
        if not fix_hashes(args.variant, args.response_dir, args.client):
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
