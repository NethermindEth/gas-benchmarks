#!/usr/bin/env python3
import argparse, json, shutil, subprocess, re, sys
from pathlib import Path

GENESIS_ROOT = "0xe8d3a308a0d3fdaeed6c196f78aad4f9620b571da6dd5b886e7fa5eba07c83e0"
IMAGES = '{"nethermind":"default","geth":"default","reth":"default","erigon":"default","besu":"default"}'


#def bump_last_nibble(h: str) -> str:
#    if not (h.startswith("0x") and len(h) > 2):
#        return h
#    try:
#        last = int(h[-1], 16)
#    except ValueError:
#        return h
#    return h[:-1] + format((last + 1) % 16, "x")


def process_line(line: str, counters: dict, bump: bool) -> str:
    line = line.rstrip("\n")
    if not line.strip():
        return "\n"
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return line + "\n"

    payload = obj["params"][0]

    if not bump:
        counters["dropped"] += 1
        return json.dumps(obj) + "\n"

    payload["stateRoot"] = GENESIS_ROOT #bump_last_nibble(GENESIS_ROOT)
    counters["bumped"] += 1
    counters["total"] += 1
    return json.dumps(obj) + "\n"


def collect_mismatches(container: str = "gas-execution-client") -> dict:
    logs = subprocess.check_output(["docker", "logs", container], stderr=subprocess.STDOUT, text=True)
    pat = re.compile(r"blockhash mismatch, want ([0-9a-f]{64}), got ([0-9a-f]{64})")
    m = {}
    for line in logs.splitlines():
        mo = pat.search(line)
        if mo:
            want_raw, got_raw = mo.group(1), mo.group(2)
            want = "0x" + want_raw
            got = "0x" + got_raw
            m[got] = want
    return m


def fix_blockhashes(tests_root: Path, mapping: dict) -> int:
    replaced_files = 0
    print("[debug] blockHash mapping:")
    for old, new in mapping.items():
        print(f"  {old!r} → {new!r}")

    for txt in tests_root.rglob("*150M*.txt"):
        text = txt.read_text()
        new_text = text
        file_changed = False
        for new, old in mapping.items():
            before = f'"blockHash": "{old}"'
            after = f'"blockHash": "{new}"'
            if before in new_text:
                file_changed = True
                print(f"[debug] {txt}: replacing {before} → {after}")
                new_text = new_text.replace(before, after)
        if file_changed:
            txt.write_text(new_text)
            replaced_files += 1

    print(f"[debug] total files changed: {replaced_files}")
    return replaced_files


def teardown(cl_name: str):
    script_dir = Path("scripts") / cl_name
    if not script_dir.is_dir():
        print(f"[!] No such directory {script_dir}, skipping teardown")
        return
    subprocess.run(["docker", "compose", "down"], cwd=script_dir, check=True)
    data_dir = script_dir / "execution-data"
    if data_dir.exists():
        subprocess.run(["rm", "-rf", str(data_dir)], check=True)


def main():
    p = argparse.ArgumentParser(description="Make warmup-tests: drop real-genesis, bump others, fix parentHash + blockHash")
    p.add_argument("-s", "--source", nargs="+", help="Legacy: Source root(s)")
    p.add_argument("-g", "--genesisPath", help="Legacy: Genesis path (used with --source)")
    p.add_argument("-j", "--sourceJson", help='New format: JSON [{"path": "tests-vm", "genesis": "...", "changeForAll": true}]')
    p.add_argument("-d", "--dest", default="warmup-tests", help="Destination root")
    p.add_argument("--changeForAll", action="store_true", help="Change stateRoot for all newPayloads (default: only last)")
    args = p.parse_args()

    test_sources = []

    if args.sourceJson:
        try:
            test_sources = json.loads(args.sourceJson)
            if not isinstance(test_sources, list):
                raise ValueError("sourceJson must be a list")
        except Exception as e:
            print(f"❌ Invalid JSON for --sourceJson: {e}")
            sys.exit(1)
    elif args.source:
        for src in args.source:
            test_sources.append({
                "path": src,
                "genesis": args.genesisPath or "",
                "changeForAll": args.changeForAll
            })
    else:
        print("❌ You must provide either --sourceJson or --source")
        sys.exit(1)

    dst_root = Path(args.dest)
    if dst_root.exists():
        shutil.rmtree(dst_root)
    dst_root.mkdir(parents=True)

    counters = {"total": 0, "bumped": 0, "dropped": 0}

    # Process each source path
    for entry in test_sources:
        src_root = Path(entry["path"])
        change_all = entry.get("changeForAll", args.changeForAll)
        prefix = src_root.name

        for src in src_root.rglob("*150M*.txt"):
            rel = src.relative_to(src_root)
            out = dst_root / prefix / rel
            out.parent.mkdir(parents=True, exist_ok=True)

            with src.open() as fin, out.open("w") as fout:
                total_payloads = sum(1 for line in fin if "engine_newPayload" in line)
                fin.seek(0)
                seen_payloads = 0

                for line in fin:
                    if "engine_newPayload" not in line:
                        continue
                    seen_payloads += 1
                    bump = change_all or (seen_payloads == total_payloads)
                    nl = process_line(line, counters, bump)
                    if nl:
                        fout.write(nl)

    print(
        f"Processed {counters['total']} payloads, "
        f"bumped {counters['bumped']} stateRoots, "
        f"dropped {counters['dropped']} into '{dst_root}'"
    )

    # Setup node with genesis if applicable
    genesis_for_geth = None
    for entry in test_sources:
        src_root = Path(entry["path"])
        relative_subdir = src_root.name
        tests_path = str(dst_root / relative_subdir)
        genesis_path = entry.get("genesis", "")
        
        setup_node_cmd = ["python3", "setup_node.py", "--client", "geth", "--imageBulk", IMAGES]
        if genesis_path:
            setup_node_cmd += ["--genesisPath", genesis_path]
        
        print(f"🔧 Setting up node for {relative_subdir} with genesis: {genesis_path or 'default'}")
        subprocess.run(setup_node_cmd, check=True)
    
        subprocess.run(
            [
                "python3", "run_kute.py",
                "--output", "generationresults",
                "--testsPath", tests_path,
                "--jwtPath", "/tmp/jwtsecret",
                "--client", "geth",
                "--run", "1"
            ],
            check=True,
        )
    
        mapping = collect_mismatches("gas-execution-client")
        if not mapping:
            print(f"⚠️  No blockhash mismatches found in {relative_subdir}; skipping fix.")
            teardown("geth")
            continue
    
        print(f"🔍 Found blockHash mismatches in {relative_subdir}:")
        print(json.dumps(mapping, indent=2))
    
        fixed = fix_blockhashes(Path(tests_path), mapping)
        print(f"✅ Replaced blockHash in {fixed} file(s) for {relative_subdir}.")
    
        teardown("geth")

    for sub in dst_root.iterdir():
        if sub.is_dir():
            for f in sub.glob("*.txt"):
                target = dst_root / f.name
                if target.exists():
                    print(f"⚠️ File already exists in root: {target}, skipping move.")
                else:
                    f.rename(target)
            sub.rmdir()


if __name__ == "__main__":
    main()
