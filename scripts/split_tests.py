import argparse
import json
import sys
from pathlib import Path


GLOBAL_PREFIX = ("gas-bump.txt", "funding.txt", "setup-global-test.txt")
GLOBAL_SUFFIX = ("teardown-global-test.txt", "current-last-global-test.txt")


def is_stateful_directory(path: Path) -> bool:
    return path.is_dir() and (path / "testing").is_dir()


def try_append(path: Path, bucket: list[Path]) -> None:
    if path.is_file() and path.suffix == ".txt":
        bucket.append(path)


def resolve_path(root: Path, phase: str, idx, name: str) -> Path | None:
    candidates = []
    if isinstance(idx, int):
        candidates.append(root / phase / f"{idx:06d}" / f"{name}.txt")
        candidates.append(root / phase / str(idx) / f"{name}.txt")
    if isinstance(idx, str) and idx:
        candidates.append(root / phase / idx / f"{name}.txt")
    candidates.append(root / phase / f"{name}.txt")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def dedupe_paths(paths: list[Path]) -> list[Path]:
    seen = set()
    ordered = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    return ordered


def collect_stateful_groups(root: Path) -> tuple[list[Path], list[list[Path]], list[Path]]:
    prefix = []
    for name in GLOBAL_PREFIX:
        try_append(root / name, prefix)

    scenario_entries: list[tuple[object, str]] = []
    order_file = root / "scenario_order.json"
    if order_file.is_file():
        try:
            data = json.loads(order_file.read_text(encoding="utf-8"))
        except Exception:
            data = []
        if isinstance(data, list):
            seen_names = set()
            for item in data:
                if isinstance(item, dict):
                    idx = item.get("index")
                    name = item.get("name")
                else:
                    idx = None
                    name = item
                if not isinstance(name, str):
                    continue
                name = name.strip()
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                scenario_entries.append((idx, name))

    testing_dir = root / "testing"
    subdirs = []
    if testing_dir.is_dir():
        subdirs = [p for p in sorted(testing_dir.iterdir()) if p.is_dir()]

    if subdirs:
        scenario_entries = []
        for scen_dir in subdirs:
            try:
                idx_value = int(scen_dir.name)
            except ValueError:
                idx_value = None
            txt_files = sorted(scen_dir.glob("*.txt"))
            if not txt_files:
                for phase in ("setup", "cleanup"):
                    phase_dir = root / phase / scen_dir.name
                    if phase_dir.is_dir():
                        txt_files = sorted(phase_dir.glob("*.txt"))
                        if txt_files:
                            break
            if not txt_files:
                continue
            for txt in txt_files:
                scenario_entries.append((idx_value, txt.stem))

    if not scenario_entries:
        names = []
        if testing_dir.is_dir():
            names = [file.stem for file in sorted(testing_dir.rglob("*.txt"))]
        if not names:
            phases = ("setup", "testing", "cleanup")
            name_set = set()
            for phase in phases:
                phase_dir = root / phase
                if not phase_dir.is_dir():
                    continue
                for file in sorted(phase_dir.rglob("*.txt")):
                    name_set.add(file.stem)
            names = sorted(name_set)
        scenario_entries = [(None, name) for name in names]

    deduped_entries = []
    seen = set()
    for idx, name in scenario_entries:
        key = (idx, name)
        if key in seen:
            continue
        seen.add(key)
        deduped_entries.append((idx, name))
    scenario_entries = deduped_entries

    scenario_entries.sort(
        key=lambda item: (item[0] if isinstance(item[0], int) else float("inf"))
    )

    scenario_groups = []
    for idx, name in scenario_entries:
        group = []
        for phase in ("setup", "testing", "cleanup"):
            path = resolve_path(root, phase, idx, name)
            if path is not None:
                group.append(path)
            elif phase == "testing":
                sys.stderr.write(f"[WARN] Missing {phase} file for scenario {name}\n")
        if group:
            scenario_groups.append(group)

    suffix = []
    for name in GLOBAL_SUFFIX:
        try_append(root / name, suffix)
    suffix.extend(sorted(root.glob("*.txt")))

    return prefix, scenario_groups, suffix


def select_groups(groups: list[list[Path]], shard_index: int, shard_total: int) -> list[list[Path]]:
    return [group for idx, group in enumerate(groups) if idx % shard_total == shard_index]


def normalize_output_path(path: Path) -> str:
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()


def main() -> int:
    parser = argparse.ArgumentParser(description="Split tests into deterministic shards.")
    parser.add_argument("--tests-path", required=True, help="Test file or directory")
    parser.add_argument("--shard-index", required=True, type=int, help="Zero-based shard index")
    parser.add_argument("--shard-total", required=True, type=int, help="Total number of shards")
    parser.add_argument("--genesis", default="", help="Genesis filename for each test entry")
    parser.add_argument("--output", default="", help="Write JSON output to this file")
    args = parser.parse_args()

    tests_path = Path(args.tests_path)
    if not tests_path.exists():
        print(f"Test path not found: {tests_path}", file=sys.stderr)
        return 1

    if args.shard_total < 1:
        print("Shard total must be >= 1", file=sys.stderr)
        return 1
    if args.shard_index < 0 or args.shard_index >= args.shard_total:
        print("Shard index must be between 0 and shard_total-1", file=sys.stderr)
        return 1

    tests: list[Path] = []

    if is_stateful_directory(tests_path):
        prefix, scenario_groups, suffix = collect_stateful_groups(tests_path)
        if scenario_groups:
            selected_groups = select_groups(scenario_groups, args.shard_index, args.shard_total)
            if selected_groups:
                tests = dedupe_paths(prefix + [p for group in selected_groups for p in group] + suffix)
        else:
            flat = dedupe_paths(prefix + suffix)
            groups = [[path] for path in flat]
            selected = select_groups(groups, args.shard_index, args.shard_total)
            tests = [path for group in selected for path in group]
    else:
        if tests_path.is_file():
            plain_files = [tests_path]
        else:
            plain_files = sorted(tests_path.rglob("*.txt"))
        groups = [[path] for path in plain_files]
        selected = select_groups(groups, args.shard_index, args.shard_total)
        tests = [path for group in selected for path in group]

    genesis = args.genesis.strip()
    payload = []
    for path in tests:
        entry = {"path": normalize_output_path(path)}
        if genesis:
            entry["genesis"] = genesis
        payload.append(entry)

    text = json.dumps(payload, separators=(",", ":"))
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
