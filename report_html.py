import argparse
import csv
import json
from pathlib import Path
from typing import Dict, Iterable, Sequence

import yaml
from bs4 import BeautifulSoup

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


def build_html_report(
    reports_dir: Path,
    computer_spec: str,
    clients: Sequence[str],
    client_results: Dict[str, Dict],
    test_cases: Dict[str, Sequence[int]],
    gas_values: Iterable[int],
    metadata: Dict[str, Dict[str, str]],
    image_overrides: Dict[str, str],
    default_images: Dict[str, str],
    method: str,
) -> Dict[str, Dict[str, list[str]]]:
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '    <meta charset="UTF-8">',
        '    <meta name="viewport" content="width=device-width, initial-scale=1.0">',
        "    <title>Benchmarking Report</title>",
        "    <style>",
        "        body { font-family: Arial, sans-serif; }",
        "        table { border-collapse: collapse; margin-bottom: 20px; }",
        "        th, td { border: 1px solid #ddd; padding: 8px; text-align: center; }",
        "        th { background-color: #f2f2f2; }",
        "        .title { text-align: left; }",
        "        .preserve-newlines { white-space: pre-wrap; }",
        "    </style>",
        "</head>",
        "<body>",
        "<h2>Computer Specs</h2>",
        f"<pre>{computer_spec}</pre>",
    ]

    csv_tables: Dict[str, Dict[str, list[str]]] = {}
    for client in clients:
        image_label = resolve_client_image(client, image_overrides, default_images)
        parts.append(f"<h1>{client.capitalize()} - {image_label} - Benchmarking Report</h1>")
        parts.append(f'<table id="table_{client}">')
        parts.append("<thead>")
        parts.append("<tr>")
        headers = [
            ("Title", False),
            ("Max (MGas/s)", True),
            ("p50 (MGas/s)", True),
            ("p95 (MGas/s)", True),
            ("p99 (MGas/s)", True),
            ("Min (MGas/s)", True),
            ("N", True),
            ("Description", False),
            ("Start Time", True),
        ]
        for idx, (label, sortable) in enumerate(headers):
            cursor = ' style="cursor: pointer;"' if sortable else ""
            parts.append(
                f'<th class="title" onclick="sortTable({idx}, \'table_{client}\', {str(sortable).lower()})"{cursor}>{label} &uarr; &darr;</th>'
                if sortable or idx == 0
                else f'<th class="title">{label}</th>'
            )
        parts.append("</tr>")
        parts.append("</thead>")
        parts.append("<tbody>")

        gas_table = get_gas_table(client_results, client, test_cases, gas_values, method, metadata)
        csv_tables[client] = gas_table
        for data in gas_table.values():
            parts.append("<tr>")
            parts.append(f'<td class="title">{data[0]}</td>')
            parts.append(f"<td>{data[2]}</td>")
            parts.append(f"<td>{data[3]}</td>")
            parts.append(f"<td>{data[4]}</td>")
            parts.append(f"<td>{data[5]}</td>")
            parts.append(f"<td>{data[1]}</td>")
            parts.append(f"<td>{data[6]}</td>")
            parts.append(f'<td style="text-align:left;">{data[7]}</td>')
            parts.append(f"<td>{data[8]}</td>")
            parts.append("</tr>")

        parts.append("</tbody>")
        parts.append("</table>")

    parts.extend(
        [
            "<script>",
            "function sortTable(columnIndex, tableId, numeric) {",
            "  const table = document.getElementById(tableId);",
            "  const tbody = table.getElementsByTagName('tbody')[0];",
            "  const rows = Array.from(tbody.rows);",
            "  const direction = table.dataset.sortDirection === 'asc' ? 'desc' : 'asc';",
            "  table.dataset.sortDirection = direction;",
            "  rows.sort((a, b) => {",
            "    const cellA = a.cells[columnIndex].innerText;",
            "    const cellB = b.cells[columnIndex].innerText;",
            "    if (numeric) {",
            "      return direction === 'asc'",
            "        ? parseFloat(cellA) - parseFloat(cellB)",
            "        : parseFloat(cellB) - parseFloat(cellA);",
            "    }",
            "    return direction === 'asc'",
            "      ? cellA.localeCompare(cellB)",
            "      : cellB.localeCompare(cellA);",
            "  });",
            "  rows.forEach(row => tbody.appendChild(row));",
            "}",
            "</script>",
            "</body>",
            "</html>",
        ]
    )

    html = "".join(parts)
    formatted_html = BeautifulSoup(html, "lxml").prettify()
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "index.html").write_text(formatted_html, encoding="utf-8")
    return csv_tables


def write_raw_results_csv(
    reports_dir: Path,
    client_results: Dict[str, Dict],
    test_cases: Dict[str, Sequence[int]],
    runs: int,
    metadata: Dict[str, Dict[str, str]],
    method: str,
) -> None:
    headers = ["Test Case", "Gas"] + [f"Run {i} Duration (ms)" for i in range(1, runs + 1)] + ["Description"]

    for client, case_map in client_results.items():
        csv_path = reports_dir / f"raw_results_{client}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(headers)
            for test_case, gas_values in test_cases.items():
                for gas in gas_values:
                    runs_values = list(case_map.get(test_case, {}).get(gas, {}).get(method, []))
                    if len(runs_values) < runs:
                        runs_values.extend("" for _ in range(runs - len(runs_values)))
                    description = metadata.get(test_case, {}).get("Description", "Description not found on metadata file")
                    title = metadata.get(test_case, {}).get("Title", test_case)
                    writer.writerow([title, gas, *runs_values, description])


def write_summary_csv(reports_dir: Path, csv_tables: Dict[str, Dict[str, list[str]]]) -> None:
    headers = ["Title", "Max (MGas/s)", "p50 (MGas/s)", "p95 (MGas/s)", "p99 (MGas/s)", "Min (MGas/s)", "N", "Description", "Start Time"]
    for client, table in csv_tables.items():
        csv_path = reports_dir / f"output_{client}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(headers)
            for data in table.values():
                writer.writerow([data[0], data[2], data[3], data[4], data[5], data[1], data[6], data[7], data[8]])


def load_metadata(tests_path: Path) -> Dict[str, Dict[str, str]]:
    metadata_path = tests_path / "metadata.json"
    if not metadata_path.is_file():
        return {}
    with metadata_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return {item["Name"]: item for item in data}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate HTML benchmarking report")
    parser.add_argument("--resultsPath", type=Path, default=Path("results/results"))
    parser.add_argument("--testsPath", type=Path, default=Path("tests/"))
    parser.add_argument(
        "--clients",
        type=str,
        default="nethermind,geth,reth,erigon,besu,nimbus,ethrex",
        help="Comma-separated list of clients",
    )
    parser.add_argument("--runs", type=int, default=8, help="Number of runs per test case")
    parser.add_argument(
        "--images",
        type=str,
        default='{"nethermind":"default","geth":"default","reth":"default","erigon":"default","besu":"default","nimbus":"default","ethrex":"default"}',
        help="JSON map of client -> docker image label",
    )
    args = parser.parse_args()

    results_path: Path = args.resultsPath
    tests_path: Path = args.testsPath
    clients = [client.strip() for client in args.clients.split(",") if client.strip()]
    runs = args.runs
    image_overrides = json.loads(args.images)

    computer_spec_path = results_path / "computer_specs.txt"
    computer_spec = computer_spec_path.read_text(encoding="utf-8") if computer_spec_path.exists() else ""
    if computer_spec:
        print(computer_spec)

    test_cases = get_test_cases(tests_path)
    gas_values = sorted({gas for gases in test_cases.values() for gas in gases})

    method = "engine_newPayloadV4"
    field = "max"
    client_results, _ = load_results_matrix(results_path, clients, test_cases, runs, method, field)

    metadata = load_metadata(tests_path)

    reports_dir = results_path / "reports"

    images_config_path = Path("images.yaml")
    default_images = {}
    if images_config_path.is_file():
        with images_config_path.open("r", encoding="utf-8") as fh:
            default_images = yaml.safe_load(fh).get("images", {})

    csv_tables = build_html_report(
        reports_dir,
        computer_spec,
        clients,
        client_results,
        test_cases,
        gas_values,
        metadata,
        image_overrides,
        default_images,
        method,
    )

    write_raw_results_csv(reports_dir, client_results, test_cases, runs, metadata, method)
    write_summary_csv(reports_dir, csv_tables)
    print(f"Report written to {reports_dir}")


if __name__ == "__main__":
    main()
