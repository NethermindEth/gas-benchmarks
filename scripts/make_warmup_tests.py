#!/usr/bin/env python3
import os
import json
import shutil
from pathlib import Path

# adjust this to your real tests root
SRC_ROOT = Path("tests")
DST_ROOT = Path("warmup-tests")

# clean out any old data
if DST_ROOT.exists():
    shutil.rmtree(DST_ROOT)
DST_ROOT.mkdir(parents=True)

def bump_hex_digit(hchar: str) -> str:
    """Increment a single hex digit (0–f) mod 16."""
    val = int(hchar, 16)
    return hex((val + 1) % 16)[2:]

for src in SRC_ROOT.rglob("*.txt"):
    # skip dirs that still have subdirs
    if any(src.parent.iterdir()):
        # ensure we're at a leaf directory
        if any(p.is_dir() for p in src.parent.iterdir()):
            continue

    rel = src.relative_to(SRC_ROOT)
    dst = DST_ROOT / rel
    dst.parent.mkdir(parents=True, exist_ok=True)

    with src.open() as fin, dst.open("w") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue

            obj = json.loads(line)
            sr = obj.get("stateRoot", "")
            if sr.startswith("0x") and len(sr) > 3:
                # bump the last hex char
                new_last = bump_hex_digit(sr[-1])
                obj["stateRoot"] = sr[:-1] + new_last

            fout.write(json.dumps(obj) + "\n")
