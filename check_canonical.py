#!/usr/bin/env python3
"""
Canonical chain integrity checker.

Walks backwards from the head (or a specified starting tag) via parentHash,
and at each step queries eth_getBlockByNumber to verify that the node's
canonical mapping matches the block reached by following hashes.

A mismatch means the node has a stale/wrong canonical marker at that height --
the bug seen in Nethermind where by-number returns a different block than
by-hash after a reorg via engine_forkchoiceUpdatedV3.

Usage:
    python3 check_canonical.py --rpc "http://127.0.0.1:8545" --depth 100
    python3 check_canonical.py --rpc "http://127.0.0.1:8545" --depth 50 --start latest
"""

import argparse
import sys
import time
import requests

BATCH_SIZE = 500


def rpc(url: str, method: str, params: list) -> dict:
    response = requests.post(url, json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }, timeout=10)
    response.raise_for_status()
    result = response.json()
    if "error" in result:
        raise RuntimeError(f"RPC error: {result['error']}")
    return result["result"]


def rpc_batch(url: str, batch: list) -> dict:
    """Send a JSON-RPC batch and return a dict keyed by request id."""
    response = requests.post(url, json=batch, timeout=30)
    response.raise_for_status()
    return {item["id"]: item.get("result") for item in response.json()}


def get_block_by_hash(url: str, block_hash: str) -> dict:
    return rpc(url, "eth_getBlockByHash", [block_hash, False])


def main():
    parser = argparse.ArgumentParser(description="Canonical chain integrity checker")
    parser.add_argument("--rpc", required=True, help="RPC URL e.g. http://127.0.0.1:8545")
    parser.add_argument("--depth", type=int, default=100,
                        help="Number of blocks to walk back (default: 100)")
    parser.add_argument("--start", default="latest",
                        help="Starting block tag: latest, finalized, safe, or a hex block number (default: latest)")
    parser.add_argument("--warn-only", action="store_true",
                        help="Log mismatches but exit 0 (don't fail the pipeline)")
    parser.add_argument("--label", default="",
                        help="Label to include in output for correlation (e.g. scenario name)")
    args = parser.parse_args()

    url = args.rpc
    label = f" [{args.label}]" if args.label else ""

    print(f"[CANONICAL-CHECK]{label} Connecting to {url}")
    print(f"[CANONICAL-CHECK]{label} Walking {args.depth} blocks back from {args.start}...\n")

    # Start from the specified block tag
    start_block = rpc(url, "eth_getBlockByNumber", [args.start, False])
    if start_block is None:
        print(f"[CANONICAL-CHECK]{label} ERROR: node returned null for '{args.start}' -- is it synced?")
        sys.exit(0 if args.warn_only else 1)

    start_number = int(start_block["number"], 16)
    start_hash = start_block["hash"]
    print(f"[CANONICAL-CHECK]{label} Start block: #{start_number}  hash={start_hash}\n")

    # Phase 1: walk backward via parentHash to build the truth chain.
    print(f"[CANONICAL-CHECK]{label} Phase 1: walking backward via parentHash...")
    phase1_start = time.monotonic()

    truth_chain = []
    current = get_block_by_hash(url, start_hash)
    if current is None:
        print(f"[CANONICAL-CHECK]{label} ERROR: could not fetch start block by hash {start_hash}")
        sys.exit(0 if args.warn_only else 1)

    for _ in range(args.depth):
        number = int(current["number"], 16)
        truth_chain.append((number, current["hash"]))

        parent_hash = current["parentHash"]
        if int(parent_hash, 16) == 0:
            print(f"[CANONICAL-CHECK]{label} Reached genesis.")
            break

        parent = get_block_by_hash(url, parent_hash)
        if parent is None:
            print(f"[CANONICAL-CHECK]{label} ERROR: parent block {parent_hash} not found -- node may be pruned.")
            break

        current = parent

    phase1_ms = (time.monotonic() - phase1_start) * 1000
    print(f"[CANONICAL-CHECK]{label}   {len(truth_chain)} block(s) collected in {phase1_ms:.0f}ms\n")

    # Phase 2: batch fetch all by-number
    print(f"[CANONICAL-CHECK]{label} Phase 2: batch fetching by number...")
    phase2_start = time.monotonic()

    numbers = [n for n, _ in truth_chain]
    by_number_map = {}
    num_batches = (len(numbers) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(numbers), BATCH_SIZE):
        chunk = numbers[i:i + BATCH_SIZE]
        batch = [
            {"jsonrpc": "2.0", "method": "eth_getBlockByNumber", "params": [hex(n), False], "id": n}
            for n in chunk
        ]
        by_number_map.update(rpc_batch(url, batch))

    phase2_ms = (time.monotonic() - phase2_start) * 1000
    print(f"[CANONICAL-CHECK]{label}   {len(numbers)} block(s) fetched in {num_batches} batch(es) in {phase2_ms:.0f}ms\n")

    # Phase 3: compare truth chain against by-number results.
    mismatches = []
    for number, chain_hash in truth_chain:
        by_number = by_number_map.get(number)
        by_number_hash = by_number["hash"] if by_number else None

        if by_number_hash != chain_hash:
            print(f"[CANONICAL-CHECK]{label}   MISMATCH at height {number}:")
            print(f"[CANONICAL-CHECK]{label}     by-hash chain : {chain_hash}")
            print(f"[CANONICAL-CHECK]{label}     by-number     : {by_number_hash}")
            mismatches.append({
                "height": number,
                "by_hash_chain": chain_hash,
                "by_number": by_number_hash,
            })
        else:
            print(f"[CANONICAL-CHECK]{label}   OK  #{number}  {chain_hash}")

    total_ms = phase1_ms + phase2_ms
    blocks_checked = len(truth_chain)
    print()
    print(f"[CANONICAL-CHECK]{label} Checked {blocks_checked} block(s) in {total_ms:.0f}ms  ({total_ms / blocks_checked:.1f}ms/block avg)")
    print()
    if mismatches:
        print(f"[CANONICAL-CHECK]{label} FOUND {len(mismatches)} MISMATCH(ES):")
        for m in mismatches:
            print(f"[CANONICAL-CHECK]{label}   height={m['height']}  chain={m['by_hash_chain']}  canonical={m['by_number']}")
        if args.warn_only:
            print(f"[CANONICAL-CHECK]{label} --warn-only is set; exiting 0 despite mismatches.")
            sys.exit(0)
        sys.exit(1)
    else:
        print(f"[CANONICAL-CHECK]{label} All {blocks_checked} block(s) consistent -- by-number matches by-hash chain.")


if __name__ == "__main__":
    main()
