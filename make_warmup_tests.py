#!/usr/bin/env python
import argparse, json, shutil, subprocess, re, sys
from pathlib import Path

GENESIS_ROOT = "0xe8d3a308a0d3fdaeed6c196f78aad4f9620b571da6dd5b886e7fa5eba07c83e0"
IMAGES = '{"nethermind":"default","geth":"ethereum/client-go:latest","reth":"default","erigon":"default","besu":"default"}'


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

    payload["stateRoot"] = GENESIS_ROOT
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


def fix_blockhashes(pattern: str, tests_root: Path, mapping: dict) -> int:
    replaced_files = 0
    print("[debug] blockHash mapping:")
    for got, want in mapping.items():
        print(f"  {got!r} → {want!r}")

    for txt in tests_root.rglob(pattern):
        text = txt.read_text()
        new_text = text
        file_changed = False
        for want, got in mapping.items():  # Corrected order
            before = f'"blockHash": "{got}"'
            after = f'"blockHash": "{want}"'
            if before in new_text:
                file_changed = True
                print(f"[debug] {txt}: replacing {before} → {after}")
                new_text = new_text.replace(before, after)
        if file_changed:
            txt.write_text(new_text)
            replaced_files += 1
        else:
            print(f"[debug] No blockHash replaced in {txt}")

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
    p = argparse.ArgumentParser(
        description="Make warmup-tests: drop real-genesis, bump others, fix parentHash + blockHash"
    )
    p.add_argument("-s", "--source", nargs="+", help="Source root(s)")
    p.add_argument(
        "-g", "--genesisPath",
        help="Path to a genesis JSON file; used to override default GENESIS_ROOT and passed to setup_node.py"
    )
    p.add_argument(
        "-j", "--sourceJson",
        help='JSON [{"path": "tests-vm", "genesis": "...", "changeForAll": true}]'
    )
    p.add_argument("-d", "--dest", default="warmup-tests", help="Destination root")
    p.add_argument(
        "--changeForAll", action="store_true",
        help="Change stateRoot for all newPayloads (default: only last)"
    )
    p.add_argument(
        "-p", "--pattern", default="*150M*.txt",
        help="Glob pattern for test files (default '*150M*.txt')"
    )
    args = p.parse_args()

    # Override GENESIS_ROOT from --genesisPath
    print("[debug] Starting warmup test generation")
    if args.genesisPath:
        try:
            with open(args.genesisPath, 'r') as gf:
                gen_data = json.load(gf)
            if 'stateRoot' not in gen_data:
                print(f"❌ Genesis file '{args.genesisPath}' missing 'stateRoot' field.")
                sys.exit(1)
            global GENESIS_ROOT
            print(f"[debug] Overriding GENESIS_ROOT:\n  before: {GENESIS_ROOT}")
            GENESIS_ROOT = gen_data['stateRoot']
            print(f"  after: {GENESIS_ROOT}")
        except Exception as e:
            print(f"❌ Error reading genesis file '{args.genesisPath}': {e}")
            sys.exit(1)

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
    pattern = args.pattern

    counters = {"total": 0, "bumped": 0, "dropped": 0}

    # Process each source path
    for entry in test_sources:
        src_root = Path(entry["path"])
        change_all = entry.get("changeForAll", args.changeForAll)
        prefix = src_root.name

        for src in src_root.rglob(pattern):
            # Skip setup/cleanup; only take testing payloads
            normalized = src.as_posix()
            if "/setup/" in normalized or "/cleanup/" in normalized:
                continue

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
    for entry in test_sources:
        src_root = Path(entry["path"])
        relative_subdir = src_root.name
        tests_path = str(dst_root / relative_subdir)
        genesis_path = entry.get("genesis", "")

        data_dir = Path("scripts/geth/execution-data").resolve()
        data_dir.mkdir(parents=True, exist_ok=True)

        setup_node_cmd = [
            sys.executable,
            "setup_node.py",
            "--client",
            "geth",
            "--imageBulk",
            IMAGES,
            "--dataDir",
            str(data_dir),
        ]
        if genesis_path:
            setup_node_cmd += ["--genesisPath", genesis_path]

        print(f"🔧 Setting up node for {relative_subdir} with genesis: {genesis_path or 'default'}")
        subprocess.run(setup_node_cmd, check=True)

        subprocess.run(
            [
                sys.executable, "run_kute.py",
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

        fixed = fix_blockhashes(pattern, Path(tests_path), mapping)
        print(f"✅ Replaced blockHash in {fixed} file(s) for {relative_subdir}.")

        teardown("geth")

    def normalize_name(path: Path) -> str:
        name = path.name
        if name.endswith(".txt"):
            name = name[:-4]
        name = re.sub(r"-gas-value(?:_[^-]+)?$", "", name)
        name = re.sub(r"opcount_[^-]+-?", "", name)
        name = re.sub(r"--+", "-", name)
        return name

    def parse_opcount_from_name(path: Path) -> int:
        m = re.search(r"opcount_([0-9]+)([kKmM]?)", path.name)
        if not m:
            return -1
        val = int(m.group(1))
        suffix = m.group(2).lower()
        if suffix == "k":
            val *= 1_000
        elif suffix == "m":
            val *= 1_000_000
        return val

    def extract_index(path: Path) -> int:
        """
        Parse the numeric scenario index from the directory structure (e.g., .../testing/000123/file.txt).
        Falls back to a large number if not found so that real indices always win.
        """
        parts = path.parts
        for i, part in enumerate(parts):
            if part.isdigit():
                try:
                    return int(part)
                except ValueError:
                    continue
        return 1_000_000_000

    # Flatten warmup-tests output directory, keeping only the highest-opcount variant per normalized test name
    for sub in list(dst_root.iterdir()):
        if not sub.is_dir():
            continue

        best_by_norm = {}
        for f in sub.rglob("*.txt"):
            norm = normalize_name(f)
            opc = parse_opcount_from_name(f)
            idx = extract_index(f)
            existing = best_by_norm.get(norm)
            if existing is None:
                best_by_norm[norm] = (f, idx, opc)
            else:
                _, best_idx, best_opc = existing
                if idx < best_idx or (idx == best_idx and opc > best_opc):
                    best_by_norm[norm] = (f, idx, opc)

        for f, _, _ in best_by_norm.values():
            target = dst_root / f.name
            if target.exists():
                target.unlink()
            target.parent.mkdir(parents=True, exist_ok=True)
            f.rename(target)

        shutil.rmtree(sub, ignore_errors=True)


if __name__ == "__main__":
    main()
