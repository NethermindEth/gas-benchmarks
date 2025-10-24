import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, Sequence

import yaml

from gas_benchmarks.reporting import get_gas_table
from gas_benchmarks.results import get_test_cases, load_results_matrix


def resolve_client_image(
    client: str, overrides: Dict[str, str], default_images: Dict[str, str]
) -> str:
    override = overrides.get(client, "")
    if override and override != "default":
        return override
    base_name = client.split("_", 1)[0]
    return default_images.get(base_name, "")


def center_string(value: object, size: int) -> str:
    text = str(value)
    if len(text) >= size:
        return text
    padding = size - len(text)
    left = padding // 2
    right = padding - left
    return " " * left + text + " " * right


def align_left_string(value: object, size: int) -> str:
    text = str(value)
    if len(text) >= size:
        return text
    return text + " " * (size - len(text))


def build_table_report(
    clients: Sequence[str],
    client_results: Dict[str, Dict],
    test_cases: Dict[str, Sequence[int]],
    gas_values: Iterable[int],
    metadata: Dict[str, Dict[str, str]],
    image_overrides: Dict[str, str],
    default_images: Dict[str, str],
    method: str,
) -> str:
    lines: list[str] = []
    header = (
        f"{center_string('Title', 68)}|"
        f"{center_string('Min (MGas/s)', 14)}|"
        f"{center_string('Max (MGas/s)', 14)}|"
        f"{center_string('p50 (MGas/s)', 14)}|"
        f"{center_string('p95 (MGas/s)', 14)}|"
        f"{center_string('p99 (MGas/s)', 14)}|"
        f"{center_string('N', 7)}|"
        f"{center_string('Description', 50)}|"
        "Start Time"
    )

    for client in clients:
        image_label = resolve_client_image(client, image_overrides, default_images)
        lines.append(f"{client.capitalize()} - {image_label} - Benchmarking Report")
        lines.append(header)
        gas_table = get_gas_table(client_results, client, test_cases, gas_values, method, metadata)
        for data in gas_table.values():
            lines.append(
                f"{align_left_string(data[0], 68)}|"
                f"{center_string(data[1], 14)}|"
                f"{center_string(data[2], 14)}|"
                f"{center_string(data[3], 14)}|"
                f"{center_string(data[4], 14)}|"
                f"{center_string(data[5], 14)}|"
                f"{center_string(data[6], 7)}|"
                f"{align_left_string(data[7], 50)}|"
                f"{data[8]}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_failure_report(
    clients: Sequence[str],
    failed_tests: Dict[str, Dict],
    test_cases: Dict[str, Sequence[int]],
    metadata: Dict[str, Dict[str, str]],
    method: str,
) -> str:
    lines: list[str] = []
    header = (
        f"{align_left_string('Title', 68)}|"
        f"{center_string('Failed', 10)}|"
        f"{center_string('Total', 10)}|"
        f"{center_string('Failure %', 12)}|"
        "Start Time"
    )

    for client in clients:
        lines.append(f"{client.capitalize()} - Failed Runs Overview")
        lines.append(header)
        client_failures = failed_tests.get(client, {})
        for test_case, gas_values in test_cases.items():
            case_failures = client_failures.get(test_case, {})
            failed = 0
            total = 0
            for gas in gas_values:
                entries = case_failures.get(gas, {}).get(method, [])
                failed += sum(1 for flag in entries if flag)
                total += len(entries)
            if total == 0:
                failure_pct = "0.0%"
            else:
                failure_pct = f"{(failed / total) * 100:.1f}%"
            title = metadata.get(test_case, {}).get("Title", test_case)
            start_time = case_failures.get("timestamp", "")
            lines.append(
                f"{align_left_string(title, 68)}|"
                f"{center_string(failed, 10)}|"
                f"{center_string(total, 10)}|"
                f"{center_string(failure_pct, 12)}|"
                f"{start_time}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def load_metadata(tests_path: Path) -> Dict[str, Dict[str, str]]:
    metadata_path = tests_path / "metadata.json"
    if not metadata_path.is_file():
        return {}
    with metadata_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return {item["Name"]: item for item in data}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate plain-text benchmarking tables")
    parser.add_argument("--resultsPath", type=Path, default=Path("results"))
    parser.add_argument("--testsPath", type=Path, default=Path("tests/"))
    parser.add_argument("--clients", type=str, default="nethermind,geth,reth")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument(
        "--images",
        type=str,
        default='{ "nethermind": "default", "besu": "default", "geth": "default", "reth": "default" , "erigon": "default"}',
    )
    args = parser.parse_args()

    results_path: Path = args.resultsPath
    tests_path: Path = args.testsPath
    clients = [client.strip() for client in args.clients.split(",") if client.strip()]
    runs = args.runs
    image_overrides = json.loads(args.images)

    spec_path = results_path / "computer_specs.txt"
    if spec_path.exists():
        print(spec_path.read_text(encoding="utf-8"))

    test_cases = get_test_cases(tests_path)
    gas_values = sorted({gas for gases in test_cases.values() for gas in gases})
    metadata = load_metadata(tests_path)

    method = "engine_newPayloadV4"
    field = "max"
    client_results, failed_tests = load_results_matrix(results_path, clients, test_cases, runs, method, field)

    images_config_path = Path("images.yaml")
    default_images = {}
    if images_config_path.is_file():
        with images_config_path.open("r", encoding="utf-8") as fh:
            default_images = yaml.safe_load(fh).get("images", {})

    reports_dir = results_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    tables_text = build_table_report(
        clients,
        client_results,
        test_cases,
        gas_values,
        metadata,
        image_overrides,
        default_images,
        method,
    )
    print(tables_text)
    (reports_dir / "tables_norm.txt").write_text(tables_text, encoding="utf-8")

    failures_text = build_failure_report(clients, failed_tests, test_cases, metadata, method)
    (reports_dir / "tables_failures.txt").write_text(failures_text, encoding="utf-8")
    print("Done!")


if __name__ == "__main__":
    main()

