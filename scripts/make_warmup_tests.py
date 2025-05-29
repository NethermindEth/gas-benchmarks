#!/usr/bin/env python3
import argparse, json, shutil
from pathlib import Path

# your real genesis root
GENESIS_ROOT = "0xe8d3a308a0d3fdaeed6c196f78aad4f9620b571da6dd5b886e7fa5eba07c83e0"

def bump_last_nibble(h: str) -> str:
    if not (h.startswith("0x") and len(h) > 2):
        return h
    try:
        last = int(h[-1], 16)
    except ValueError:
        return h
    return h[:-1] + format((last + 1) % 16, 'x')

def process_line(line: str, counters: dict) -> str:
    line = line.rstrip("\n")
    if not line.strip():
        return "\n"
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return line + "\n"

    if obj.get("method") == "engine_newPayloadV3":
        payload = obj["params"][0]
        sr = payload.get("stateRoot")
        # drop any payload that already uses the real genesis root
        if sr == GENESIS_ROOT:
            counters["dropped"] += 1
            return ""  # skip this line entirely

        # otherwise force genesis root + bump, then write
        payload["stateRoot"] = bump_last_nibble(GENESIS_ROOT)
        counters["bumped"] += 1

    counters["total"] += 1
    return json.dumps(obj) + "\n"

def main():
    p = argparse.ArgumentParser(
        description="Make warmup-tests: drop real-genesis blocks, bump others"
    )
    p.add_argument("-s","--source",default="tests", help="Source root")
    p.add_argument("-d","--dest",  default="warmup-tests", help="Destination root")
    args = p.parse_args()

    src_root = Path(args.source)
    dst_root = Path(args.dest)

    if dst_root.exists():
        shutil.rmtree(dst_root)
    dst_root.mkdir(parents=True)

    counters = {"total":0, "bumped":0, "dropped":0}

    for src in src_root.rglob("*.txt"):
        rel = src.relative_to(src_root)
        out = dst_root / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        with src.open() as fin, out.open("w") as fout:
            for line in fin:
                new_line = process_line(line, counters)
                if new_line:  # only write non-empty
                    fout.write(new_line)

    print(
        f"Processed {counters['total']} lines, "
        f"bumped {counters['bumped']} payloads, "
        f"dropped {counters['dropped']} real-root payloads into '{dst_root}'"
    )

if __name__=="__main__":
    main()
