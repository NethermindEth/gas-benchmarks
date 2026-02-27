#!/usr/bin/env python
import argparse, atexit, json, os, shutil, signal, subprocess, re, sys, time
from pathlib import Path

GENESIS_ROOT = "0xe8d3a308a0d3fdaeed6c196f78aad4f9620b571da6dd5b886e7fa5eba07c83e0"
IMAGES = '{"nethermind":"default","geth":"ethereum/client-go:latest","reth":"default","erigon":"default","besu":"default"}'
KUTE_BINARY = Path("./nethermind/tools/artifacts/bin/Nethermind.Tools.Kute/release/Nethermind.Tools.Kute")
WARMUP_NETHERMIND_LOG = Path("warmup_nethermind.log")
WARMUP_CLIENT = "nethermind"
ZFS_RUNTIME_DATASET_ROOT = "gasbench-runtime"
ZFS_SNAPSHOT_PREFIX = "gasbench_tmp"

_ACTIVE_CLEANUP = {
    "client": WARMUP_CLIENT,
    "data_dir": None,
    "overlay": None,
}


def _set_active_cleanup(data_dir: Path = None, overlay: dict = None, client: str = WARMUP_CLIENT) -> None:
    _ACTIVE_CLEANUP["client"] = client
    _ACTIVE_CLEANUP["data_dir"] = data_dir
    _ACTIVE_CLEANUP["overlay"] = overlay


def _clear_active_cleanup() -> None:
    _ACTIVE_CLEANUP["data_dir"] = None
    _ACTIVE_CLEANUP["overlay"] = None


def _run_active_cleanup() -> None:
    data_dir = _ACTIVE_CLEANUP.get("data_dir")
    overlay = _ACTIVE_CLEANUP.get("overlay")
    if data_dir is None and overlay is None:
        return
    try:
        teardown(str(_ACTIVE_CLEANUP.get("client") or WARMUP_CLIENT), data_dir=data_dir, overlay=overlay)
    except Exception as e:
        print(f"[warn] active cleanup failed: {e}")
    finally:
        _clear_active_cleanup()


def _sig_handler(signum, _frame) -> None:
    print(f"[info] Caught signal {signum}; running cleanup")
    _run_active_cleanup()
    raise SystemExit(128 + signum)


atexit.register(_run_active_cleanup)
for _sig in (signal.SIGINT, signal.SIGTERM):
    try:
        signal.signal(_sig, _sig_handler)
    except Exception:
        pass
if hasattr(signal, "SIGHUP"):
    try:
        signal.signal(signal.SIGHUP, _sig_handler)
    except Exception:
        pass


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


def _is_hex32(value: str) -> bool:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) != 66:
        return False
    try:
        int(value[2:], 16)
        return True
    except ValueError:
        return False


def _ensure_kute_binary() -> None:
    if KUTE_BINARY.exists():
        return
    print(f"[warn] Kute binary not found at {KUTE_BINARY}. Running `make prepare_tools`.")
    subprocess.run(["make", "prepare_tools"], check=True)
    if not KUTE_BINARY.exists():
        raise RuntimeError(f"Kute binary still not found after prepare_tools: {KUTE_BINARY}")


_NM_BLOCKHASH_MISMATCH_RE = re.compile(
    r"Invalid block hash\s+(0x[0-9a-fA-F]{64})\s+does not match calculated hash\s+(0x[0-9a-fA-F]{64})",
    re.IGNORECASE,
)


def _iter_dicts(value):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _iter_dicts(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dicts(item)


def _extract_validation_errors_from_payload(payload):
    errors = []
    for node in _iter_dicts(payload):
        for key in ("validationError", "validation_error"):
            raw = node.get(key)
            if isinstance(raw, str) and raw:
                errors.append(raw)
    return errors


def _parse_response_payloads(raw: str):
    raw = raw.strip()
    if not raw:
        return []

    payloads = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(raw):
        if raw[idx] in (' ', '\t', '\n', '\r'):
            idx += 1
            continue
        try:
            obj, end = decoder.raw_decode(raw, idx)
            payloads.append(obj)
            idx = end
        except json.JSONDecodeError:
            idx += 1
    return payloads


def collect_mismatches_from_kute(response_dir: Path, log_path: Path = None, scope: str = "") -> dict:
    mapping = {}
    response_files = sorted(response_dir.glob(f"{WARMUP_CLIENT}_response_*.txt"))
    if not response_files:
        print(f"[warn] No {WARMUP_CLIENT} response files found in {response_dir}")
        return mapping

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    for response_file in response_files:
        try:
            raw = response_file.read_text(encoding="utf-8")
        except Exception as exc:
            print(f"[warn] Failed to read {response_file}: {exc}")
            continue

        if log_path is not None:
            with log_path.open("a", encoding="utf-8") as f:
                label = scope or "global"
                f.write(f"\n===== {label} | {response_file.name} =====\n")
                f.write(raw)
                if not raw.endswith("\n"):
                    f.write("\n")

        payloads = _parse_response_payloads(raw)
        for payload in payloads:
            for validation_error in _extract_validation_errors_from_payload(payload):
                mo = _NM_BLOCKHASH_MISMATCH_RE.search(validation_error)
                if not mo:
                    continue
                got = mo.group(1).lower()
                want = mo.group(2).lower()
                mapping[got] = want

    return mapping


def fix_blockhashes(pattern: str, tests_root: Path, mapping: dict) -> int:
    normalized_mapping = {}
    for got, want in mapping.items():
        if isinstance(got, str) and isinstance(want, str):
            normalized_mapping[got.lower()] = want.lower()

    replaced_files = 0
    replaced_payloads = 0

    for txt in tests_root.rglob(pattern):
        try:
            lines = txt.read_text(encoding="utf-8").splitlines(keepends=True)
        except Exception as exc:
            print(f"[warn] Failed to read {txt}: {exc}")
            continue

        new_lines = []
        file_changed = False
        file_replaced = 0
        for line in lines:
            stripped = line.strip()
            if not stripped:
                new_lines.append(line)
                continue

            line_body = line.rstrip("\r\n")
            line_suffix = line[len(line_body):]
            try:
                obj = json.loads(line_body)
            except json.JSONDecodeError:
                new_lines.append(line)
                continue

            replaced_this_line = False
            if isinstance(obj, dict):
                params = obj.get("params")
                if isinstance(params, list) and params and isinstance(params[0], dict):
                    payload = params[0]
                    current = payload.get("blockHash")
                    if isinstance(current, str):
                        replacement = normalized_mapping.get(current.lower())
                        if replacement and replacement != current.lower():
                            payload["blockHash"] = replacement
                            replaced_this_line = True

            if replaced_this_line:
                file_changed = True
                file_replaced += 1
                new_lines.append(json.dumps(obj, separators=(",", ":")) + line_suffix)
            else:
                new_lines.append(line)

        if file_changed:
            txt.write_text("".join(new_lines), encoding="utf-8")
            replaced_files += 1
            replaced_payloads += file_replaced
    print(f"[info] blockHash patching complete: files changed={replaced_files}, payload lines replaced={replaced_payloads}")
    return replaced_files


def _dir_has_content(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def _resolve_snapshot_lower(snapshot_root: Path, network, client: str) -> Path:
    snapshot_root = snapshot_root.expanduser().resolve()

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

    if _dir_has_content(snapshot_root):
        return snapshot_root

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

def _run_zfs_command(args, capture_output: bool = False) -> str:
    cmd = ["zfs", *args]
    kwargs = {"check": True, "text": True}
    if capture_output:
        kwargs["capture_output"] = True

    try:
        result = subprocess.run(cmd, **kwargs)
        return result.stdout if capture_output else ""
    except (subprocess.CalledProcessError, FileNotFoundError):
        if not (hasattr(os, "geteuid") and os.geteuid() != 0 and shutil.which("sudo")):
            raise
        sudo_cmd = ["sudo", *cmd]
        result = subprocess.run(sudo_cmd, **kwargs)
        return result.stdout if capture_output else ""


def _resolve_zfs_dataset_for_path(target: Path) -> tuple[str, Path]:
    target_abs = target.resolve()
    output = _run_zfs_command(["list", "-H", "-o", "name,mountpoint", "-t", "filesystem"], capture_output=True)

    best_dataset = ""
    best_mount = None
    best_len = -1

    for raw in output.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split("\t")
        if len(parts) != 2:
            continue
        dataset, mountpoint = parts
        if mountpoint in ("-", "none", "legacy"):
            continue

        mount_path = Path(mountpoint)
        try:
            mount_abs = mount_path.resolve()
        except Exception:
            continue

        mount_str = str(mount_abs)
        target_str = str(target_abs)
        if target_str == mount_str or target_str.startswith(mount_str + os.sep):
            if len(mount_str) > best_len:
                best_len = len(mount_str)
                best_dataset = dataset
                best_mount = mount_abs

    if not best_dataset or best_mount is None:
        raise RuntimeError(f"Unable to resolve ZFS dataset for path: {target_abs}")

    return best_dataset, best_mount


def _ensure_zfs_parent_dataset(pool: str, client: str) -> Path:
    base_dataset = f"{pool}/{ZFS_RUNTIME_DATASET_ROOT}"
    client_dataset = f"{base_dataset}/{client}"

    for dataset in (base_dataset, client_dataset):
        try:
            _run_zfs_command(["list", "-H", "-o", "name", dataset], capture_output=True)
        except Exception:
            _run_zfs_command(["create", "-o", "mountpoint=none", dataset])

    return Path(client_dataset)


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
        "backend": "overlay",
        "root": overlay_root,
        "merged": merged,
        "upper": upper,
        "work": work,
    }


def _prepare_zfs_clone_for_client(snapshot_root: Path, network, runtime_root: Path, client: str):
    lower = _resolve_snapshot_lower(snapshot_root, network, client)
    lower_abs = lower.resolve()

    dataset, mountpoint = _resolve_zfs_dataset_for_path(lower_abs)
    lower_suffix = Path(".")
    if lower_abs != mountpoint:
        lower_suffix = lower_abs.relative_to(mountpoint)

    runtime_base = _overlay_base_from_lower(lower_abs, runtime_root)
    clone_id = f"{time.time_ns()}_{os.getpid()}"
    clone_mount_root = runtime_base / client / clone_id
    clone_mount_root.parent.mkdir(parents=True, exist_ok=True)
    clone_mount_root.mkdir(parents=True, exist_ok=True)

    pool = dataset.split("/", 1)[0]
    _ensure_zfs_parent_dataset(pool, client)

    clone_dataset = f"{pool}/{ZFS_RUNTIME_DATASET_ROOT}/{client}/{clone_id}"
    snapshot_name = f"{dataset}@{ZFS_SNAPSHOT_PREFIX}_{client}_{clone_id}"

    _run_zfs_command(["snapshot", snapshot_name])
    try:
        _run_zfs_command(["clone", "-o", f"mountpoint={clone_mount_root}", snapshot_name, clone_dataset])
    except Exception:
        _run_zfs_command(["destroy", snapshot_name])
        raise

    data_dir = clone_mount_root if lower_suffix == Path(".") else (clone_mount_root / lower_suffix)
    if not data_dir.exists():
        _run_zfs_command(["destroy", "-r", "-f", clone_dataset])
        _run_zfs_command(["destroy", snapshot_name])
        raise RuntimeError(f"ZFS clone data dir missing: {data_dir}")

    return {
        "backend": "zfs",
        "lower": lower_abs,
        "root": clone_mount_root,
        "data_dir": data_dir,
        "dataset": clone_dataset,
        "snapshot": snapshot_name,
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

def _cleanup_zfs_clone(runtime: dict) -> None:
    dataset = runtime.get("dataset")
    snapshot_name = runtime.get("snapshot")
    root = runtime.get("root")

    if dataset:
        try:
            _run_zfs_command(["destroy", "-r", "-f", str(dataset)])
        except Exception as exc:
            print(f"[warn] failed to destroy ZFS clone dataset {dataset}: {exc}")

    if snapshot_name:
        try:
            _run_zfs_command(["destroy", str(snapshot_name)])
        except Exception:
            pass

    if root and Path(root).exists():
        shutil.rmtree(root, ignore_errors=True)
        parent = Path(root).parent
        try:
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        except Exception:
            pass


def _cleanup_runtime(runtime: dict) -> None:
    backend = str(runtime.get("backend") or "overlay").lower()
    if backend == "zfs":
        _cleanup_zfs_clone(runtime)
    else:
        _cleanup_overlay(runtime)


def teardown(cl_name: str, data_dir: Path = None, overlay: dict = None):
    script_dir = Path("scripts") / cl_name
    if not script_dir.is_dir():
        print(f"[!] No such directory {script_dir}, skipping teardown")
        return
    down = subprocess.run(["docker", "compose", "down"], cwd=script_dir, check=False)
    if down.returncode != 0:
        print(f"[warn] docker compose down returned {down.returncode} in {script_dir}")
    if overlay:
        _cleanup_runtime(overlay)
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
        help="Path to a genesis JSON file; if it has top-level stateRoot it overrides fallback GENESIS_ROOT, and is passed to setup_node.py"
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
        help="Enable overlayfs and use this snapshot root as lowerdir for nethermind",
    )
    p.add_argument(
        "--overlayRoot",
        default="overlay-runtime",
        help="Overlay runtime root (relative to snapshot parent unless absolute)",
    )
    p.add_argument(
        "--snapshotBackend",
        default="overlay",
        choices=["overlay", "zfs"],
        help="Snapshot backend (overlay or zfs) used when --snapshotRoot is set",
    )
    p.add_argument(
        "--network",
        help="Optional network name to resolve snapshot subdirectories (and passed to setup_node.py)",
    )
    p.add_argument(
        "--filter",
        default="",
        help="Only regenerate warmups for test files whose names contain this substring (preserves existing warmups for other tests)",
    )
    args = p.parse_args()
    _ensure_kute_binary()

    # Optionally override GENESIS_ROOT from --genesisPath when a valid top-level stateRoot exists.
    genesis_state_root_applied = False
    if args.genesisPath:
        try:
            with open(args.genesisPath, 'r') as gf:
                gen_data = json.load(gf)
            global GENESIS_ROOT
            state_root = gen_data.get("stateRoot")
            if isinstance(state_root, str) and _is_hex32(state_root):
                GENESIS_ROOT = state_root
                genesis_state_root_applied = True
            else:
                print(
                    f"[warn] Genesis file '{args.genesisPath}' has no valid top-level stateRoot; "
                    f"keeping fallback value {GENESIS_ROOT}"
                )
        except Exception as e:
            print(f"❌ Error reading genesis file '{args.genesisPath}': {e}")
            sys.exit(1)
    print(
        f"[info] Genesis patch status: "
        f"genesis_path={'set' if args.genesisPath else 'not-set'}, "
        f"state_root_from_genesis={'yes' if genesis_state_root_applied else 'no'}, "
        f"active_state_root={GENESIS_ROOT}"
    )

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
    warmup_filter = args.filter.strip() if args.filter else ""
    if dst_root.exists():
        if warmup_filter:
            print(f"[INFO] --filter is set ('{warmup_filter}'); preserving existing warmup directory")
        else:
            shutil.rmtree(dst_root)
    dst_root.mkdir(parents=True, exist_ok=True)
    pattern = args.pattern

    counters = {"total": 0, "bumped": 0, "dropped": 0}
    WARMUP_NETHERMIND_LOG.write_text("", encoding="utf-8")
    use_snapshot_runtime = bool(args.snapshotRoot)
    snapshot_root = Path(args.snapshotRoot).expanduser() if use_snapshot_runtime else None
    overlay_root = Path(args.overlayRoot).expanduser()
    if not use_snapshot_runtime and args.overlayRoot != "overlay-runtime":
        print("[error] --overlayRoot requires --snapshotRoot")
        sys.exit(1)
    if use_snapshot_runtime and snapshot_root and not snapshot_root.exists():
        print(f"[error] Snapshot root does not exist: {snapshot_root}")
        sys.exit(1)
    if use_snapshot_runtime and args.snapshotBackend == "zfs" and not shutil.which("zfs"):
        print("[error] zfs backend requested but 'zfs' command is not available")
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
            if warmup_filter and warmup_filter.lower() not in src.name.lower():
                continue

            rel = src.relative_to(src_root)
            out = dst_root / prefix / rel
            out.parent.mkdir(parents=True, exist_ok=True)

            with src.open() as fin, out.open("w") as fout:
                payload_lines = [line for line in fin if "engine_newPayload" in line]
                total_payloads = len(payload_lines)

                for idx, line in enumerate(payload_lines, start=1):
                    bump = change_all or (idx == total_payloads)
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
            if use_snapshot_runtime and snapshot_root is not None:
                if args.snapshotBackend == "zfs":
                    overlay = _prepare_zfs_clone_for_client(snapshot_root, args.network, overlay_root, WARMUP_CLIENT)
                    data_dir = Path(overlay["data_dir"]).resolve()
                    print(f"[info] Using zfs clone data dir: {data_dir}")
                else:
                    overlay = _prepare_overlay_for_client(snapshot_root, args.network, overlay_root, WARMUP_CLIENT)
                    data_dir = Path(overlay["merged"]).resolve()
                    print(f"[info] Using overlay data dir: {data_dir}")
            else:
                data_dir = Path(f"scripts/{WARMUP_CLIENT}/execution-data").resolve()
                data_dir.mkdir(parents=True, exist_ok=True)
            _set_active_cleanup(data_dir=data_dir, overlay=overlay, client=WARMUP_CLIENT)

            setup_node_cmd = [
                sys.executable,
                "setup_node.py",
                "--client",
                WARMUP_CLIENT,
                "--imageBulk",
                IMAGES,
                "--dataDir",
                str(data_dir),
                "--dataBackend",
                args.snapshotBackend if use_snapshot_runtime else "direct",
            ]
            if args.network:
                setup_node_cmd += ["--network", args.network]
            if genesis_path:
                setup_node_cmd += ["--genesisPath", genesis_path]

            print(f"[info] Setting up node for {relative_subdir} with genesis: {genesis_path or 'default'}")
            subprocess.run(setup_node_cmd, check=True)

            response_output_dir = Path("generationresults") / relative_subdir
            if response_output_dir.exists():
                shutil.rmtree(response_output_dir, ignore_errors=True)
            response_output_dir.mkdir(parents=True, exist_ok=True)

            subprocess.run(
                [
                    sys.executable, "run_kute.py",
                    "--output", str(response_output_dir),
                    "--testsPath", tests_path,
                    "--jwtPath", "/tmp/jwtsecret",
                    "--client", WARMUP_CLIENT,
                    "--run", "1"
                ],
                check=True,
            )

            mapping = collect_mismatches_from_kute(
                response_output_dir,
                log_path=WARMUP_NETHERMIND_LOG,
                scope=relative_subdir,
            )
            if not mapping:
                print(f"[warn] No blockhash mismatches found in {relative_subdir}; skipping fix.")
                continue

            print(f"[info] Found blockHash mismatches in {relative_subdir}:")
            print(json.dumps(mapping, indent=2))

            fixed = fix_blockhashes(pattern, Path(tests_path), mapping)
            print(f"[info] Replaced blockHash in {fixed} file(s) for {relative_subdir}.")
        finally:
            teardown(WARMUP_CLIENT, data_dir=data_dir, overlay=overlay)
            _clear_active_cleanup()

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

