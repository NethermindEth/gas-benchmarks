#!/usr/bin/env python3
import argparse, json, shutil
from pathlib import Path

def bump_last_nibble(h: str) -> str:
    """If h is a hex string like '0x...', bump its last nibble."""
    if not (h.startswith("0x") and len(h) > 2):
        return h
    last = h[-1]
    try:
        new_last = format((int(last, 16) + 1) % 16, 'x')
        return h[:-1] + new_last
    except ValueError:
        return h

def main():
    p = argparse.ArgumentParser(
        description="Mirror tests/*.txt → warmup-tests/*.txt, bumping each JSON's stateRoot nibble"
    )
    p.add_argument("-s", "--source", default="tests", help="Source tests root")
    p.add_argument("-d", "--dest",   default="warmup-tests", help="Destination root")
    args = p.parse_args()

    src_root = Path(args.source)
    dst_root = Path(args.dest)

    # Recreate dest
    if dst_root.exists():
        shutil.rmtree(dst_root)
    dst_root.mkdir(parents=True)

    total_lines = 0
    bumped = 0

    for src in src_root.rglob("*.txt"):
        rel = src.relative_to(src_root)
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        with src.open("r") as fin, dst.open("w") as fout:
            for line in fin:
                total_lines += 1
                line = line.rstrip("\n")
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    fout.write(line + "\n")
                    continue

                if isinstance(obj, dict) and "stateRoot" in obj and isinstance(obj["stateRoot"], str):
                    orig = obj["stateRoot"]
                    new = bump_last_nibble(orig)
                    if new != orig:
                        bumped += 1
                        obj["stateRoot"] = new

                fout.write(json.dumps(obj) + "\n")

    print(f"Processed {total_lines} lines; bumped {bumped} stateRoot values into '{dst_root}'")

if __name__ == "__main__":
    main()
