#!/usr/bin/env python
import argparse, json, os, shutil, subprocess, re, sys, time
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


def _dir_has_content(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def _resolve_snapshot_lower(snapshot_root: Path, network, client: str) -> Path:
    snapshot_root = snapshot_root.expanduser().resolve()
    if _dir_has_content(snapshot_root):
        return snapshot_root

    candidates = []
    if network:
        network_lower = str(network).lower()
        candidates.extend(
            [
                snapshot_root / str(network) / client,
                snapshot_root / network_lower / client,
                snapshot_root / str(network),
                snapshot_root / network_lower,
            ]
        )
    candidates.append(snapshot_root / client)

    for candidate in candidates:
        if _dir_has_content(candidate):
            return candidate

    raise RuntimeError(f"Unable to locate snapshot directory for {client} under {snapshot_root}")


def _is_mounted(mount_point: Path) -> bool:
    try:
        abs_path = mount_point.resolve()
        with open("/proc/mounts", "r", encoding="utf-8") as mounts:
            for line in mounts:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == str(abs_path):
                    return True
    except Exception:
        return False
    return False


def _mount_overlay(lower: Path, upper: Path, work: Path, merged: Path) -> None:
    lower = lower.resolve()
    upper = upper.resolve()
    work = work.resolve()
    merged = merged.resolve()

    if not lower.exists() or not any(lower.iterdir()):
        raise RuntimeError(f"Lower dir {lower} missing or empty; download snapshot first.")

    upper.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    merged.mkdir(parents=True, exist_ok=True)

    mount_opts = f"lowerdir={lower},upperdir={upper},workdir={work},redirect_dir=on"
    cmd = ["mount", "-t", "overlay", "overlay", "-o", mount_opts, str(merged)]
    if hasattr(os, "geteuid") and os.geteuid() != 0 and shutil.which("sudo"):
        cmd = ["sudo"] + cmd
    subprocess.run(cmd, check=True)


def _unmount_overlay(merged: Path) -> None:
    if not _is_mounted(merged):
        return

    cmd = ["umount", str(merged)]
    if hasattr(os, "geteuid") and os.geteuid() != 0 and shutil.which("sudo"):
        cmd = ["sudo"] + cmd
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        lazy_cmd = ["umount", "-l", str(merged)]
        if hasattr(os, "geteuid") and os.geteuid() != 0 and shutil.which("sudo"):
            lazy_cmd = ["sudo"] + lazy_cmd
        subprocess.run(lazy_cmd, check=False)


def _overlay_base_from_lower(lower: Path, overlay_root: Path) -> Path:
    if overlay_root.is_absolute():
        return overlay_root
    return lower.parent / overlay_root


def _prepare_overlay_for_client(snapshot_root: Path, network, overlay_root: Path, client: str):
    lower = _resolve_snapshot_lower(snapshot_root, network, client)
    overlay_base = _overlay_base_from_lower(lower, overlay_root)
    overlay_base_lower = [part.lower() for part in overlay_base.parts]
    if not any("overlay" in part for part in overlay_base_lower):
        overlay_base = overlay_base / "overlay-runtime"

    overlay_id = f"{time.time_ns()}_{os.getpid()}"
    overlay_root = overlay_base / client / overlay_id
    merged = overlay_root / "merged"
    upper = overlay_root / "upper"
    work = overlay_root / "work"

    overlay_root.parent.mkdir(parents=True, exist_ok=True)

    if _is_mounted(merged):
        _unmount_overlay(merged)

    if overlay_root.exists():
        shutil.rmtree(overlay_root, ignore_errors=True)

    _mount_overlay(lower, upper, work, merged)

    return {
        "lower": lower,
        "root": overlay_root,
        "merged": merged,
        "upper": upper,
        "work": work,
    }


def _cleanup_overlay(overlay: dict) -> None:
    merged = overlay.get("merged")
    root = overlay.get("root")

    if merged:
        _unmount_overlay(merged)
        if _is_mounted(merged):
            print(f"[warn] Unable to unmount overlay at {merged}; leaving mount in place")
            return

    if root and Path(root).exists():
        shutil.rmtree(root, ignore_errors=True)
        client_root = Path(root).parent
        base_root = client_root.parent
        for path in (client_root, base_root):
            try:
                if path.exists() and not any(path.iterdir()):
                    path.rmdir()
            except Exception:
                pass


def teardown(cl_name: str, data_dir: Path = None, overlay: dict = None):
    script_dir = Path("scripts") / cl_name
    if not script_dir.is_dir():
        print(f"[!] No such directory {script_dir}, skipping teardown")
        return
    subprocess.run(["docker", "compose", "down"], cwd=script_dir, check=True)
    if overlay:
        _cleanup_overlay(overlay)
        return
    if data_dir is None:
        data_dir = script_dir / "execution-data"
    if data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)


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
    p.add_argument(
        "--snapshotRoot",
        help="Enable overlayfs and use this snapshot root as lowerdir for geth",
    )
    p.add_argument(
        "--overlayRoot",
        default="overlay-runtime",
        help="Overlay runtime root (relative to snapshot parent unless absolute)",
    )
    p.add_argument(
        "--network",
        help="Optional network name to resolve snapshot subdirectories (and passed to setup_node.py)",
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
    use_overlay = bool(args.snapshotRoot)
    snapshot_root = Path(args.snapshotRoot).expanduser() if use_overlay else None
    overlay_root = Path(args.overlayRoot).expanduser()
    if not use_overlay and args.overlayRoot != "overlay-runtime":
        print("[error] --overlayRoot requires --snapshotRoot")
        sys.exit(1)
    if use_overlay and snapshot_root and not snapshot_root.exists():
        print(f"[error] Snapshot root does not exist: {snapshot_root}")
        sys.exit(1)

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

        overlay = None
        data_dir = None
        try:
            if use_overlay and snapshot_root is not None:
                overlay = _prepare_overlay_for_client(snapshot_root, args.network, overlay_root, "geth")
                data_dir = Path(overlay["merged"]).resolve()
                print(f"[debug] Using overlay data dir: {data_dir}")
            else:
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
            if args.network:
                setup_node_cmd += ["--network", args.network]
            if genesis_path:
                setup_node_cmd += ["--genesisPath", genesis_path]

            print(f"[info] Setting up node for {relative_subdir} with genesis: {genesis_path or 'default'}")
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
                print(f"[warn] No blockhash mismatches found in {relative_subdir}; skipping fix.")
                continue

            print(f"[info] Found blockHash mismatches in {relative_subdir}:")
            print(json.dumps(mapping, indent=2))

            fixed = fix_blockhashes(pattern, Path(tests_path), mapping)
            print(f"[info] Replaced blockHash in {fixed} file(s) for {relative_subdir}.")
        finally:
            teardown("geth", data_dir=data_dir, overlay=overlay)

    # Flatten all generated warmup files into a single top-level directory
    # (e.g., warmup-repricing/*.txt), keeping the latest copy on name collision.
    all_txts = list(dst_root.rglob("*.txt"))
    for txt in all_txts:
        target = dst_root / txt.name
        if target.resolve() == txt.resolve():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        txt.rename(target)

    # Remove any now-empty subdirectories
    for path in sorted(dst_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            try:
                next(path.iterdir())
            except StopIteration:
                path.rmdir()

if __name__ == "__main__":
    main()
