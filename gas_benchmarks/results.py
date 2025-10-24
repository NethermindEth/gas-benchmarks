from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, Mapping, Sequence, Tuple

from .models import SectionData
from .system import convert_dotnet_ticks_to_utc

SECTION_SEPARATOR = "--------------------------------------------------------------"


def read_results(text: str) -> Dict[str, SectionData]:
    """
    Parse a benchmark results file into a mapping of measurement name to data.
    """
    sections: Dict[str, SectionData] = {}
    if not text:
        return sections

    for chunk in text.split(SECTION_SEPARATOR):
        if not chunk.strip():
            continue

        timestamp: int | None = None
        measurement: str | None = None
        tags: Dict[str, str] = {}
        fields: Dict[str, str] = {}

        for block in chunk.split("#"):
            if not block:
                continue

            if block.startswith(" TIMESTAMP:"):
                try:
                    timestamp = int(block.split(":", 1)[1])
                except (IndexError, ValueError):
                    timestamp = None
            elif block.startswith(" MEASUREMENT:"):
                parts = block.split()
                if len(parts) >= 4:
                    measurement = parts[3].strip()
            elif block.startswith(" TAGS:"):
                for line in block.splitlines()[1:]:
                    if not line:
                        continue
                    key, _, value = line.partition(" = ")
                    if key:
                        tags[key.strip()] = value.strip()
            elif block.startswith(" FIELDS:"):
                for line in block.splitlines()[1:]:
                    if not line:
                        continue
                    key, _, value = line.partition(" = ")
                    if key:
                        fields[key.strip()] = value.strip()

        if timestamp is not None and measurement:
            sections[measurement] = SectionData(timestamp, measurement, tags, fields)

    return sections


def check_sync_status(json_line: str) -> bool:
    """
    Return True if the JSON-RPC payload reports a VALID payload status.
    """
    try:
        data = json.loads(json_line)
    except json.JSONDecodeError:
        return False

    result = data.get("result")
    if not isinstance(result, dict):
        return False

    status = result.get("status")
    if isinstance(status, str):
        return status.upper() == "VALID"

    payload_status = result.get("payloadStatus")
    if isinstance(payload_status, dict):
        payload_status_value = payload_status.get("status")
        if isinstance(payload_status_value, str):
            return payload_status_value.upper() == "VALID"
    return False


def extract_response_and_result(
    results_path: str | Path,
    client: str,
    test_case_name: str,
    gas_used: int | str,
    run: int,
    method: str,
    field: str,
) -> Tuple[bool, float, int]:
    """
    Load the response/result pair for a single run.

    Returns:
        tuple(valid_response, metric_value, timestamp)
    """
    base_path = Path(results_path)
    gas_suffix = f"{gas_used}M" if str(gas_used).isdigit() else str(gas_used)
    result_file = base_path / f"{client}_results_{run}_{test_case_name}_{gas_suffix}.txt"
    response_file = base_path / f"{client}_response_{run}_{test_case_name}_{gas_suffix}.txt"

    if not result_file.exists():
        print(f"[WARN] Missing results: {result_file}")
        return False, 0.0, 0
    if not response_file.exists():
        print(f"[WARN] Missing response: {response_file}")
        return False, 0.0, 0

    response_is_valid = True
    response_text = response_file.read_text(encoding="utf-8")
    if not response_text.strip():
        print(f"[WARN] Empty response file: {response_file}")
        response_is_valid = False
    else:
        for line in response_text.splitlines():
            if not line.strip():
                continue
            if not check_sync_status(line):
                print(f"[WARN] Invalid sync status in {response_file}")
                response_is_valid = False
                break

    section_map = read_results(result_file.read_text(encoding="utf-8"))
    section = section_map.get(method)
    if section is None:
        available = ", ".join(section_map) or "none"
        print(
            f"[WARN] Method '{method}' missing in {result_file}. "
            f"Available: {available}"
        )
        timestamp = next(iter(section_map.values()), SectionData(0, "", {}, {})).timestamp if section_map else 0
        return False, 0.0, timestamp

    try:
        metric_value = float(section.fields[field])
    except (KeyError, TypeError, ValueError):
        print(f"[WARN] Field '{field}' missing in {result_file}")
        return response_is_valid, 0.0, section.timestamp

    return response_is_valid, metric_value, section.timestamp


def check_client_response_is_valid(
    results_path: str | Path, client: str, test_case: str, run_count: int
) -> bool:
    """
    Verifies that responses for a given test case/run have VALID payloads.
    """
    base_path = Path(results_path)
    for index in range(1, run_count + 1):
        response_file = base_path / f"{client}_response_{index}_{test_case}"
        if not response_file.exists():
            return False
        response_text = response_file.read_text(encoding="utf-8")
        if not response_text.strip():
            return False
        for line in response_text.splitlines():
            if not line.strip():
                continue
            if not check_sync_status(line):
                return False
    return True


def get_test_cases(tests_path: str | Path) -> Dict[str, list[int]]:
    """
    Discover test-case gas values from the standard directory layout.
    """
    root_path = Path(tests_path)
    test_cases: Dict[str, set[int]] = defaultdict(set)
    pattern = re.compile(r"(?P<base>.+?)_(?P<gas>[0-9]+)M\.txt$")

    for path in root_path.rglob("*.txt"):
        if "testing" not in path.parts:
            continue
        match = pattern.match(path.name)
        if match:
            name = match.group("base")
            gas_value = int(match.group("gas"))
        else:
            name = path.stem
            gas_value = 60
        test_cases[name].add(gas_value)

    return {name: sorted(values) for name, values in test_cases.items()}


def iter_response_files(results_path: str | Path, client: str, test_case: str) -> Iterable[Path]:
    """
    Yield response files for a given client/test case following the naming convention.
    """
    base_path = Path(results_path)
    prefix = f"{client}_response_"
    for path in sorted(base_path.glob(f"{prefix}*_{test_case}.txt")):
        yield path


def load_results_matrix(
    results_path: str | Path,
    clients: Sequence[str],
    test_cases: Mapping[str, Sequence[int]],
    runs: int,
    method: str,
    field: str,
) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
    """
    Load the nested client/test-case/gas structure used by the reporting scripts.
    """
    client_results: Dict[str, Dict] = {}
    failed_tests: Dict[str, Dict] = {}

    for client in clients:
        client_case_map: Dict[str, Dict] = {}
        failure_case_map: Dict[str, Dict] = {}

        for test_case, gas_values in test_cases.items():
            case_entry: Dict = {}
            failure_entry: Dict = {}
            latest_timestamp = 0

            for gas in gas_values:
                case_entry[gas] = {method: []}
                failure_entry[gas] = {method: []}
                for run_index in range(1, runs + 1):
                    response_ok, metric_value, timestamp = extract_response_and_result(
                        results_path,
                        client,
                        test_case,
                        gas,
                        run_index,
                        method,
                        field,
                    )
                    case_entry[gas][method].append(metric_value)
                    failure_entry[gas][method].append(not response_ok)
                    if timestamp:
                        latest_timestamp = timestamp

            formatted_ts = convert_dotnet_ticks_to_utc(latest_timestamp) if latest_timestamp else 0
            case_entry["timestamp"] = formatted_ts
            failure_entry["timestamp"] = formatted_ts

            client_case_map[test_case] = case_entry
            failure_case_map[test_case] = failure_entry

        client_results[client] = client_case_map
        failed_tests[client] = failure_case_map

    return client_results, failed_tests
