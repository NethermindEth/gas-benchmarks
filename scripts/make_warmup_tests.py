#!/usr/bin/env python3
import json, shutil
from pathlib import Path

SRC_ROOT = Path("tests-vm")
DST_ROOT = Path("warmup-tests")

# Recreate warmup-tests/
if DST_ROOT.exists():
    shutil.rmtree(DST_ROOT)
DST_ROOT.mkdir(parents=True)

def bump_last_nibble(hroot: str) -> str:
    """Given a hex string starting with 0x, bump the last hex digit mod16."""
    if not hroot.startswith("0x") or len(hroot) < 3:
        return hroot
    last = hroot[-1]
    try:
        new_last = format((int(last, 16) + 1) % 16, 'x')
        return hroot[:-1] + new_last
    except ValueError:
        return hroot

for src in SRC_ROOT.rglob("*.txt"):
    rel = src.relative_to(SRC_ROOT)
    dst = DST_ROOT / rel
    dst.parent.mkdir(parents=True, exist_ok=True)

    with src.open() as fin, dst.open("w") as fout:
        for line in fin:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                fout.write(line + "\n")
                continue

            if "stateRoot" in obj:
                obj["stateRoot"] = bump_last_nibble(obj["stateRoot"])
            fout.write(json.dumps(obj) + "\n")

print(f"✓ Generated {sum(1 for _ in DST_ROOT.rglob('*.txt'))} files in {DST_ROOT}")
