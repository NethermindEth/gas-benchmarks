#!/usr/bin/env python3
import argparse, json, shutil, subprocess, re, sys
from pathlib import Path

# your real genesis roots
GENESIS_ROOT   = "0xe8d3a308a0d3fdaeed6c196f78aad4f9620b571da6dd5b886e7fa5eba07c83e0"
GENESIS_PARENT = "0x9cbea0de83b440f4462c8280a4b0b4590cdb452069757e2c510cb3456b6c98cc"

# your image‐bulk JSON
IMAGES = '{"nethermind":"default","geth":"default","reth":"default","erigon":"default","besu":"default"}'

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

        # 1) skip payload that already uses the real genesis root
        if not payload["blockNumber"] == "0x3":
            counters["dropped"] += 1
            return json.dumps(obj) + "\n"

        # 2) otherwise bump stateRoot
        payload["stateRoot"] = bump_last_nibble(GENESIS_ROOT)
        counters["bumped"] += 1
    else:
        # drop every non-newPayloadV3
        counters["dropped"] += 1
        return ""

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
    pat = re.compile(r'blockhash mismatch, want ([0-9a-f]{64}), got ([0-9a-f]{64})')
    m = {}
    for line in logs.splitlines():
        mo = pat.search(line)
        if mo:
            want_raw, got_raw = mo.group(1), mo.group(2)
            want = "0x" + want_raw
            got  = "0x" + got_raw
            m[got] = want
    return m
    
def fix_blockhashes(tests_root: Path, mapping: dict) -> int:
    """
    In-place: for every .txt under tests_root, replace
      "blockHash": "<old>"
    with
      "blockHash": "<new>"
    according to mapping {old: new}.
    Returns number of files changed.
    """
    replaced_files = 0

    # Debug: print the mapping so you can confirm the exact keys
    print("[debug] blockHash mapping:")
    for old, new in mapping.items():
        print(f"  {old!r} → {new!r}")

    for txt in tests_root.rglob("*150M*.txt"):
        text = txt.read_text()
        new_text = text
        file_changed = False

        for new, old in mapping.items():
            # build the exact phrase we want to swap
            before = f'"blockHash": "{old}"'
            after  = f'"blockHash": "{new}"'
            if before in new_text:
                file_changed = True
                print(f"[debug] {txt}: replacing {before} → {after}")
                new_text = new_text.replace(before, after)

        if file_changed:
            txt.write_text(new_text)
            replaced_files += 1

    print(f"[debug] total files changed: {replaced_files}")
    return replaced_files

def chain_parenthashes(tests_root: Path, genesis_parent: str) -> int:
    """
    In-place: for every .txt under tests_root, parse each engine_newPayloadV3,
    and set its parentHash to the previous payload's blockHash (or genesis_parent for the first).
    Returns the number of files modified.
    """
    changed_files = 0

    for txt in tests_root.rglob("*.txt"):
        prev = genesis_parent
        new_lines = []
        file_changed = False

        for raw in txt.read_text().splitlines(keepends=True):
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                new_lines.append(raw)
                continue

            if obj.get("method") == "engine_newPayloadV3":
                payload = obj["params"][0]
                old_parent = payload.get("parentHash")
                # if it's not already the correct prev, patch it
                if old_parent != prev:
                    payload["parentHash"] = prev
                    file_changed = True
                # now bump prev to this payload's blockHash
                if prev == genesis_parent:
                    prev = payload.get("blockHash", prev)

                new_lines.append(json.dumps(obj) + "\n")
            else:
                new_lines.append(raw)

        if file_changed:
            txt.write_text("".join(new_lines))
            changed_files += 1

    return changed_files

def teardown(cl_name: str):
    script_dir = Path("scripts") / cl_name
    if not script_dir.is_dir():
        print(f"[!] No such directory {script_dir}, skipping teardown")
        return
    subprocess.run(["docker", "compose", "down"], cwd=script_dir, check=True)
    data_dir = script_dir / "execution-data"
    if data_dir.exists():
        subprocess.run(["sudo", "rm", "-rf", str(data_dir)], check=True)

def main():
    p = argparse.ArgumentParser(
        description="Make warmup-tests: drop real-genesis, bump others, fix parentHash + blockHash"
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

    # 1) scan + bump + force parentHash
    for src in src_root.rglob("*150M*.txt"):
        rel = src.relative_to(src_root)
        out = dst_root / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        with src.open() as fin, out.open("w") as fout:
            for line in fin:
                nl = process_line(line, counters)
                if nl:
                    fout.write(nl)
                     
    print(
        f"Processed {counters['total']} payloads, "
        f"bumped {counters['bumped']} stateRoots, "
        f"dropped {counters['dropped']} into '{dst_root}'"
    )    

    # 2) spin up node & send invalid payloads
    subprocess.run(
        ["python3", "setup_node.py", "--client", "geth", "--imageBulk", IMAGES],
        check=True
    )
    subprocess.run([
        "python3", "run_kute.py",
        "--output", "generationresults",
        "--testsPath", str(dst_root),
        "--jwtPath", "/tmp/jwtsecret",
        "--client", "geth",
        "--run", "1"
    ], check=True)

    # 3) collect mismatches & patch only blockHash fields
    mapping = collect_mismatches("gas-execution-client")
    if not mapping:
        print("⚠️  No blockhash mismatches found; nothing to fix.")
        teardown("geth")
        return

    print("🔍 Found blockHash mismatches:", json.dumps(mapping, indent=2))
    fixed = fix_blockhashes(dst_root, mapping)
    print(f"✅ Replaced blockHash in {fixed} test file(s).")

    # 4) cleanup docker & data
    teardown("geth")

if __name__ == "__main__":
    main()
