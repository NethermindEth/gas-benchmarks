#!/usr/bin/env python3
import argparse, json, shutil, subprocess, re
from pathlib import Path

# your real genesis root
GENESIS_ROOT = "0xe8d3a308a0d3fdaeed6c196f78aad4f9620b571da6dd5b886e7fa5eba07c83e0"
IMAGES='{"nethermind":"default","geth":"default","reth":"default","erigon":"default","besu":"default"}'

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
    else:
        counters["dropped"] += 1
        return "" # skip any line which is not newPayload as is not needed for warming

    counters["total"] += 1
    return json.dumps(obj) + "\n"

def collect_mismatches(container: str = "gas-execution-client") -> dict:
    """
    Parse `docker logs <container>` for blockhash mismatches and return
    a dict mapping 'got' -> 'want'.
    """
    logs = subprocess.check_output(
        ["docker", "logs", container],
        stderr=subprocess.STDOUT,
        text=True,
    )
    pattern = re.compile(
        r'blockhash mismatch, want ([0-9a-f]{64}), got ([0-9a-f]{64})'
    )
    mapping = {}
    for line in logs.splitlines():
        m = pattern.search(line)
        if m:
            want, got = m.group(1), m.group(2)
            mapping[got] = want
    return mapping

def fix_blockhashes(tests_root: Path, mapping: dict) -> int:
    """
    In-place replace all occurrences of each 'got' hash in mapping.keys()
    with its 'want' in every .txt under tests_root. Returns number of files changed.
    """
    replaced_files = 0
    for txt in tests_root.rglob("*.txt"):
        text = txt.read_text()
        new_text = text
        for got, want in mapping.items():
            new_text = new_text.replace(got, want)
        if new_text != text:
            txt.write_text(new_text)
            replaced_files += 1
    return replaced_files

def teardown(cl_name: str):
    # compute the directory
    script_dir = Path("scripts") / cl_name
    if not script_dir.is_dir():
        raise FileNotFoundError(f"Directory not found: {script_dir}")

    # 1) docker compose down
    subprocess.run(
        ["docker", "compose", "down"],
        cwd=script_dir,
        check=True
    )

    # 2) sudo rm -rf execution-data
    exec_data = script_dir / "execution-data"
    if exec_data.exists():
        subprocess.run(
            ["sudo", "rm", "-rf", str(exec_data)],
            check=True
        )
    else:
        print(f"[i] No execution-data directory at {exec_data}")

def main():
    p = argparse.ArgumentParser(
        description="Make warmup-tests: drop real-genesis blocks, bump others"
    )
    p.add_argument("-s","--source",default="tests-vm", help="Source root")
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

    # Generate infra, send all invalid payloads, capture from logs valid block_hash, regenerate warmup tests
    subprocess.run(
        [
            "python3", "setup_node.py", 
            "--client", "geth",
            "--imageBulk", IMAGES,
        ],
        check=True
    )
    subprocess.run(
        [
            "python3", "run_kute.py",
            "--output", "generationresults",
            "--testsPath", str(dst_root),
            "--jwtPath", "/tmp/jwtsecret",
            "--client", "geth",
            "--run", "1"
        ],
        check=True
    )
    
    mapping = collect_mismatches("gas-execution-client")
    if not mapping:
        print("⚠️  No blockhash mismatches found in container logs; nothing to fix.")
        return
    fixed = fix_blockhashes(dst_root, mapping)
    print(f"Replaced blockhash in {fixed} test files ({len(mapping)} distinct mismatches).")

    #cleanup
    teardown("geth")

if __name__=="__main__":
    main()
