#!/usr/bin/env python3
import argparse, json, shutil, os
from pathlib import Path

# your real genesis root
GENESIS_ROOT = "0xe8d3a308a0d3fdaeed6c196f78aad4f9620b571da6dd5b886e7fa5eba07c83e0"

def bump_last_nibble(h: str) -> str:
    if not (h.startswith("0x") and len(h) > 2): return h
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
        # force the valid root, then bump it
        payload["stateRoot"] = bump_last_nibble(GENESIS_ROOT)
        counters["bumped"] += 1

    counters["total"] += 1
    return json.dumps(obj) + "\n"

def main():
    p = argparse.ArgumentParser()
    p.add_argument("-s","--source",default="tests")
    p.add_argument("-d","--dest",  default="warmup-tests")
    args = p.parse_args()

    src = Path(args.source)
    dst = Path(args.dest)
    if dst.exists(): shutil.rmtree(dst)
    dst.mkdir(parents=True)

    counters = {"total":0, "bumped":0}

    for txt in src.rglob("*.txt"):
        rel = txt.relative_to(src)
        out = dst/rel
        out.parent.mkdir(exist_ok=True, parents=True)
        with txt.open() as fin, out.open("w") as fout:
            for line in fin:
                fout.write(process_line(line, counters))

    print(f"Processed {counters['total']} payload lines, bumped {counters['bumped']} stateRoots into {dst}")

if __name__=="__main__":
    main()
