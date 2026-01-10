#!/usr/bin/env python3
"""
Download the latest benchmark fixtures from the execution-specs repository,
cache the release metadata to avoid repeat downloads, and emit JSON-RPC payload
files following the stateful test directory layout:

  <output>/
    setup/000001/<scenario>.txt
    testing/000001/<scenario>.txt

If a scenario produces multiple engine_newPayload calls, every payload except the
last is written to the setup file (each as a newline-delimited JSON object) and
the final payload is written to the testing file. Single-payload scenarios are
written only to testing.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tarfile
from pathlib import Path
from typing import Dict, Iterable, Tuple

import requests

GITHUB_API = "https://api.github.com/repos/ethereum/execution-specs/releases"
BENCHMARK_PREFIX = "benchmark@v"
ASSET_NAME = "fixtures_benchmark.tar.gz"
CACHE_FILE = "release_cache.json"

SCENARIO_INDICES: Dict[str, int] = {}


def fetch_benchmark_releases() -> list[dict]:
    resp = requests.get(GITHUB_API)
    resp.raise_for_status()
    releases = resp.json()
    bench = [r for r in releases if r.get("tag_name", "").startswith(BENCHMARK_PREFIX)]
    return bench


def select_release(tag: str | None) -> dict:
    releases = fetch_benchmark_releases()
    if not releases:
        raise RuntimeError("No benchmark releases found")

    def verkey(r: dict) -> tuple[int, ...]:
        return tuple(int(x) for x in r["tag_name"].split("@v", 1)[1].split("."))

    if tag:
        for r in releases:
            if r.get("tag_name") == tag:
                return r
        available = ", ".join(r.get("tag_name", "?") for r in releases)
        raise RuntimeError(f"Benchmark release '{tag}' not found. Available: {available}")

    releases.sort(key=verkey, reverse=True)
    return releases[0]


def download_asset(url: str, dest: Path) -> None:
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)


def extract_tarball(archive: Path, to: Path) -> None:
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(path=to)


def normalize_name(raw: str) -> Tuple[str, str]:
    m = re.search(r"-gas-value_[^\]-]+", raw)
    if m:
        tok = m.group(0)
        base = raw.replace(tok, "", 1)
        return base, tok
    return raw, ""


def safe_filename(name: str) -> str:
    return re.sub(r'[<>:\\"/\\|?*]', "_", name)


def assign_index(raw: str) -> int:
    base, suffix = normalize_name(raw)
    key = f"{base}{suffix}"
    if key not in SCENARIO_INDICES:
        SCENARIO_INDICES[key] = len(SCENARIO_INDICES) + 1
    return SCENARIO_INDICES[key]


def ensure_phase_dirs(base_dir: Path, index: int) -> Tuple[Path, Path]:
    dir_name = f"{index:06d}"
    setup_dir = base_dir / "setup" / dir_name
    testing_dir = base_dir / "testing" / dir_name
    for directory in (setup_dir, testing_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return setup_dir, testing_dir


def should_exclude(raw: str, exclude_patterns: Iterable[str]) -> bool:
    for pat in exclude_patterns:
        if pat and (pat in raw or re.search(pat, raw)):
            return True
    return False


def iter_cases(data, default_name: str) -> Iterable[Tuple[str, dict]]:
    if isinstance(data, dict):
        for name, case in data.items():
            yield name, case
    elif isinstance(data, list):
        for i, case in enumerate(data):
            name = case.get("name") or f"{default_name}_case_{i}"
            yield name, case


def extract_new_payloads(case: dict) -> list[tuple[str, str]]:
    payload_pairs: list[tuple[str, str]] = []
    ZERO32 = "0x" + ("00" * 32)

    for entry in case.get("engineNewPayloads", []):
        params = entry.get("params", [])
        version = entry.get("newPayloadVersion") or "1"
        method = f"engine_newPayloadV{version}"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        np_json = json.dumps(payload, separators=(",", ":"))

        block = params[0] if params and isinstance(params[0], dict) else {}
        block_hash = block.get("blockHash")
        if not isinstance(block_hash, str) or not block_hash.startswith("0x"):
            block_hash = ZERO32

        forkchoice_version = entry.get("forkchoiceVersion")
        if not forkchoice_version:
            try:
                np_version = int(version)
            except (TypeError, ValueError):
                np_version = 3
            if np_version >= 3:
                forkchoice_version = "3"
            elif np_version == 2:
                forkchoice_version = "2"
            else:
                forkchoice_version = "1"

        fcu_method = f"engine_forkchoiceUpdatedV{forkchoice_version}"
        state = {
            "headBlockHash": block_hash,
            "safeBlockHash": ZERO32,
            "finalizedBlockHash": ZERO32,
        }
        fcu_params: list = [state]
        try:
            if int(forkchoice_version) >= 2:
                fcu_params.append(None)
        except ValueError:
            fcu_params.append(None)
        fcu_json = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": fcu_method,
                "params": fcu_params,
            },
            separators=(",", ":"),
        )
        payload_pairs.append((np_json, fcu_json))
    return payload_pairs


def write_payloads(
    output_dir: Path,
    scenario_name: str,
    payload_pairs: list[tuple[str, str]],
) -> None:
    index = assign_index(scenario_name)
    setup_dir, testing_dir = ensure_phase_dirs(output_dir, index)

    base, suffix = normalize_name(scenario_name)
    filename = safe_filename(f"{base}{suffix}.txt")

    if len(payload_pairs) > 1:
        setup_path = setup_dir / filename
        with setup_path.open("w", encoding="utf-8") as f:
            for np_line, fcu_line in payload_pairs[:-1]:
                f.write(np_line)
                f.write("\n")
                f.write(fcu_line)
                f.write("\n")
        print(f"[INFO] Wrote setup payloads: {setup_path}")
    else:
        setup_path = setup_dir / filename
        if setup_path.exists():
            setup_path.unlink()

    testing_path = testing_dir / filename
    last_np, last_fcu = payload_pairs[-1]
    with testing_path.open("w", encoding="utf-8") as f:
        f.write(last_np)
        f.write("\n")
        f.write(last_fcu)
        f.write("\n")
    print(f"[INFO] Wrote testing payload: {testing_path}")

def process_fixture_dir(root: Path, outdir: Path, excludes: list[str]) -> None:
    for path in sorted(root.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for raw, case in iter_cases(data, path.stem):
            if should_exclude(raw, excludes):
                print(f"- Skipped: {raw}")
                continue

            payload_lines = extract_new_payloads(case)
            if not payload_lines:
                continue

            write_payloads(outdir, raw, payload_lines)


def load_cached_tag(cache_path: Path) -> str:
    if cache_path.is_file():
        data = json.loads(cache_path.read_text())
        return data.get("tag_name", "")
    return ""


def save_cached_tag(cache_path: Path, tag: str) -> None:
    cache_path.write_text(json.dumps({"tag_name": tag}), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser("Generate RPC cases from benchmark fixtures")
    parser.add_argument("-o", "--output-dir", type=Path, default=Path.cwd() / "rpc_cases")
    parser.add_argument("-t", "--temp-dir", type=Path, default=Path.cwd() / "tmp")
    parser.add_argument(
        "-x",
        "--exclude",
        action="append",
        default=[],
        help="Comma-separated substrings or regexes to exclude (can repeat)",
    )
    parser.add_argument(
        "--release-tag",
        help="Specific benchmark release tag (e.g. benchmark@v1.2.3). Uses latest if omitted.",
    )
    parser.add_argument(
        "--local-archive",
        type=Path,
        help="Path to local fixtures_benchmark.tar.gz archive (skips download)",
    )
    args = parser.parse_args()

    flat_excludes: list[str] = []
    for chunk in args.exclude:
        for pattern in chunk.split(","):
            pattern = pattern.strip()
            if pattern:
                flat_excludes.append(pattern)
    args.exclude = flat_excludes
    print(f"ℹ️ excluding patterns: {args.exclude}")

    args.temp_dir.mkdir(exist_ok=True)
    if args.output_dir.exists():
        shutil.rmtree(args.output_dir)
    args.output_dir.mkdir(exist_ok=True)

    if args.local_archive:
        archive = args.local_archive
        if not archive.exists():
            raise RuntimeError(f"Local archive not found: {archive}")
        print(f"Using local archive: {archive}")
        print(f"Extracting to {args.temp_dir}...")
        extract_tarball(archive, args.temp_dir)
    else:
        release = select_release(args.release_tag)
        if args.release_tag:
            print(f"Using benchmark release: {args.release_tag}")
        tag = release["tag_name"]
        print(f"Selected benchmark release: {tag}")

        cache_path = args.temp_dir / CACHE_FILE
        if load_cached_tag(cache_path) == tag:
            print(f"Release {tag} already processed. Skipping download.")
        else:
            asset = next((a for a in release["assets"] if a["name"] == ASSET_NAME), None)
            if not asset:
                raise RuntimeError(f"Asset {ASSET_NAME} missing in release {tag}")
            archive = args.temp_dir / ASSET_NAME
            print(f"Downloading {ASSET_NAME}...")
            download_asset(asset["browser_download_url"], archive)
            print(f"Extracting to {args.temp_dir}...")
            extract_tarball(archive, args.temp_dir)
            save_cached_tag(cache_path, tag)

    bench_path = args.temp_dir / "fixtures" / "blockchain_tests_engine_x" / "benchmark"
    if not bench_path.exists():
        raise RuntimeError(f"Cannot find fixtures at {bench_path}")
    process_fixture_dir(bench_path, args.output_dir, args.exclude)


if __name__ == "__main__":
    main()
