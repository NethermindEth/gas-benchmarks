#!/usr/bin/env python3
import argparse, json, shutil
from pathlib import Path

def bump_last_nibble(h: str) -> str:
    """Increment the final hex digit of a 0x... string, mod 16."""
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

    # Only touch engine_newPayloadV3
    if obj.get("method") == "engine_newPayloadV3" and isinstance(obj.get("params"), list):
        params = obj["params"]
        if params and isinstance(params[0], dict):
            payload = params[0]
            sr = payload.get("stateRoot")
            if isinstance(sr, str):
                new = bump_last_nibble(sr)
                if new != sr:
                    payload["stateRoot"] = new
                    counters["bumped"] += 1

    counters["total_lines"] += 1
    return json.dumps(obj) + "\n"

def main():
    p = argparse.ArgumentParser(
        description="Mirror tests → warmup-tests, bumping only engine_newPayloadV3.stateRoot"
    )
    p.add_argument("-s", "--source", default="tests", help="Source directory")
    p.add_argument("-d", "--dest",   default="warmup-tests", help="Destination directory")
    args = p.parse_args()

    src_root = Path(args.source)
    dst_root = Path(args.dest)

    if dst_root.exists():
        shutil.rmtree(dst_root)
    dst_root.mkdir(parents=True)

    counters = {"total_lines": 0, "bumped": 0}

    for src in src_root.rglob("*.txt"):
        rel = src.relative_to(src_root)
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        with src.open() as fin, dst.open("w") as fout:
            for line in fin:
                fout.write(process_line(line, counters))

    print(f"Processed {counters['total_lines']} payload lines; bumped {counters['bumped']} stateRoot values into '{dst_root}'")

if __name__ == "__main__":
    main()
