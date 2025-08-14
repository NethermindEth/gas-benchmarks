#!/usr/bin/env python3
"""
Download the latest or prerelease "benchmark" fixtures from the Ethereum execution-spec-tests repo,
cache the release metadata to avoid re-downloading the same version,
extract each "engineNewPayloads" test scenario into a separate JSON-RPC payload file,
and name them by test identifier with the gas-value token moved to the end.
Supports filtering out tests by substring or regex via --exclude flags.
Uses the repo's `utils/make_rpc.jq` for envelope construction.
"""
import argparse
import json
import re
import subprocess
import tarfile
import shutil
from pathlib import Path

import requests

GITHUB_API = "https://api.github.com/repos/ethereum/execution-spec-tests/releases"
BENCHMARK_PREFIX = "benchmark@v"
ASSET_NAME = "fixtures_benchmark.tar.gz"
CACHE_FILE = "release_cache.json"


def fetch_latest_benchmark_release() -> dict:
    resp = requests.get(GITHUB_API)
    resp.raise_for_status()
    releases = resp.json()
    bench = [r for r in releases if r.get("tag_name", "").startswith(BENCHMARK_PREFIX)]
    if not bench:
        raise RuntimeError("No benchmark releases found")

    def verkey(r):
        return tuple(int(x) for x in r['tag_name'].split("@v", 1)[1].split('.'))

    bench.sort(key=verkey, reverse=True)
    return bench[0]


def download_asset(url: str, dest: Path):
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(dest, 'wb') as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)


def extract_tarball(archive: Path, to: Path):
    with tarfile.open(archive, 'r:gz') as tf:
        tf.extractall(path=to)


def normalize_name(raw: str) -> (str, str):
    m = re.search(r"-gas-value_[^\]-]+", raw)
    if m:
        tok = m.group(0)
        base = raw.replace(tok, '', 1)
        return base, tok
    return raw, ''


def locate_utils_jq(utils_dir: Path) -> Path:
    path = utils_dir / 'make_rpc.jq'
    if not path.is_file():
        raise FileNotFoundError(f"make_rpc.jq not found in {utils_dir}")
    return path


def should_exclude(raw: str, exclude_patterns: list[str]) -> bool:
    """Return True if raw matches any of the exclude patterns (substring or regex)."""
    for pat in exclude_patterns:
        if pat in raw or re.search(pat, raw):
            return True
    return False


def process_fixture_dir(root: Path, outdir: Path, jq_filter: Path, excludes: list[str]):
    for path in root.rglob('*.json'):
        proc = subprocess.run([
            'jq', '-c', '-f', str(jq_filter), str(path)
        ], capture_output=True, text=True, check=True)
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        if not lines:
            continue

        data = json.loads(path.read_text())
        if isinstance(data, dict):
            items = list(data.items())
        elif isinstance(data, list):
            items = [(elt.get('name', f'case_{i}'), elt) for i, elt in enumerate(data)]
        else:
            continue

        for (raw, _), line in zip(items, lines):
            if should_exclude(raw, excludes):
                print(f"- Skipped: {raw}")
                continue

            base, suffix = normalize_name(raw)
            fname = f"{base}{suffix}.txt"
            safe = re.sub(r'[<>:\\"/\\|?*]', '_', fname)
            outpath = outdir / safe
            outdir.mkdir(parents=True, exist_ok=True)
            with open(outpath, 'w', encoding='utf-8') as f:
                f.write(line + '\n')
            print(f"→ Wrote: {outpath}")


def load_cached_tag(cache_path: Path) -> str:
    if cache_path.is_file():
        data = json.loads(cache_path.read_text())
        return data.get('tag_name', '')
    return ''


def save_cached_tag(cache_path: Path, tag: str):
    cache_path.write_text(json.dumps({'tag_name': tag}), encoding='utf-8')


def main():
    p = argparse.ArgumentParser("Generate RPC cases from benchmark fixtures")
    p.add_argument('-o', '--output-dir', type=Path, default=Path.cwd() / 'rpc_cases')
    p.add_argument('-t', '--temp-dir', type=Path, default=Path.cwd() / 'tmp')
    p.add_argument('--utils-dir', type=Path, default=Path(__file__).parent / 'utils',
                   help='Where to find make_rpc.jq')
    p.add_argument(
        '-x', '--exclude',
        action='append',
        default=[],
        help='Comma-separated substrings or regexes to exclude (can repeat)')
    args = p.parse_args()

    # —— flatten & strip —— #
    flat = []
    for chunk in args.exclude:
        for pat in chunk.split(','):
            pat = pat.strip()
            if pat:
                flat.append(pat)
    args.exclude = flat
    print(f"ℹ️ excluding patterns: {args.exclude}")

    args.temp_dir.mkdir(exist_ok=True)
    if args.output_dir.exists():
        shutil.rmtree(args.output_dir)
    args.output_dir.mkdir(exist_ok=True)
    jq_filter = locate_utils_jq(args.utils_dir)

    rel = fetch_latest_benchmark_release()
    tag = rel['tag_name']
    print(f"Latest benchmark release: {tag}")

    cache_path = args.temp_dir / CACHE_FILE
    if load_cached_tag(cache_path) == tag:
        print(f"Release {tag} already processed. Skipping download.")
    else:
        asset = next((a for a in rel['assets'] if a['name'] == ASSET_NAME), None)
        if not asset:
            raise RuntimeError(f"Asset {ASSET_NAME} missing in release {tag}")
        archive = args.temp_dir / ASSET_NAME
        print(f"Downloading {ASSET_NAME}...")
        download_asset(asset['browser_download_url'], archive)
        print(f"Extracting to {args.temp_dir}...")
        extract_tarball(archive, args.temp_dir)
        save_cached_tag(cache_path, tag)

    bench_path = args.temp_dir / 'fixtures' / 'blockchain_tests_engine_x' / 'benchmark'
    if not bench_path.exists():
        raise RuntimeError(f"Cannot find fixtures at {bench_path}")
    process_fixture_dir(bench_path, args.output_dir, jq_filter, args.exclude)


if __name__ == '__main__':
    main()
