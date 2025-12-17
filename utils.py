import json
import math
import os
import re
import logging
from collections import defaultdict
from typing import Dict, Optional

import cpuinfo
import platform

import numpy as np
import psutil
from bs4 import BeautifulSoup
import datetime

logger = logging.getLogger(__name__)

def read_results(text):
    sections = {}
    for i, sections_text in enumerate(text.split('--------------------------------------------------------------')):
        # print("Processing section: " + str(i))
        timestamp = None
        measurement = None
        tags = {}
        fields = {}
        for full_lines in sections_text.split('#'):
            if not full_lines:
                continue

            if full_lines.startswith(' TIMESTAMP:'):
                timestamp = int(full_lines.split(':')[1])
            elif full_lines.startswith(' MEASUREMENT:'):
                # Take everything after "MEASUREMENT: " to handle multi-word measurements
                measurement = full_lines.split('MEASUREMENT:')[1].split('\n')[0].strip()
            elif full_lines.startswith(' TAGS:'):
                for line in full_lines.split('\n')[1:]:
                    if not line:
                        continue
                    data = line.strip().split(' = ')
                    tags[data[0]] = data[1]
                pass
            elif full_lines.startswith(' FIELDS:'):
                for line in full_lines.split('\n')[1:]:
                    if not line:
                        continue
                    data = line.strip().split(' = ')
                    fields[data[0]] = data[1]

        if timestamp is not None and measurement is not None:
            sections[measurement] = SectionData(timestamp, measurement, tags, fields)

    return sections


def extract_response_and_result(
    results_path,
    client,
    test_case_name,
    gas_used,
    run,
    method,
    field,
    result_token: Optional[str] = None,
):
    """
    Read the response/result files for a single run.

    The file name pattern historically used a '<test>_<gas>M' suffix, but some
    scenarios (e.g. all_scenarios_for_analysis) no longer include a gas
    suffix in the filename. We therefore try multiple candidates in order:
    - explicit result_token (the actual test filename without extension)
    - legacy '<test_case_name>_<gas_used>M'
    - bare '<test_case_name>'
    """

    candidate_suffixes = []
    if result_token:
        candidate_suffixes.append(result_token)
    candidate_suffixes.append(f"{test_case_name}_{gas_used}M")
    candidate_suffixes.append(test_case_name)

    result_file = None
    response_file = None
    seen_suffixes = set()
    for suffix in candidate_suffixes:
        if suffix in seen_suffixes:
            continue
        seen_suffixes.add(suffix)

        potential_result = f"{results_path}/{client}_results_{run}_{suffix}.txt"
        potential_response = f"{results_path}/{client}_response_{run}_{suffix}.txt"

        if os.path.exists(potential_result) and os.path.exists(potential_response):
            result_file = potential_result
            response_file = potential_response
            break

    response = True
    result = 0
    if not result_file or not os.path.exists(result_file):
        print("No result")
        return False, 0, 0, 0, 0, 0
    if not response_file or not os.path.exists(response_file):
        print("No repsonse")
        return False, 0, 0, 0, 0, 0
    # Get the responses from the files
    with open(response_file, 'r') as file:
        text = file.read()
        if len(text) == 0:
            print("text len 0")
            return False, 0, 0, 0, 0, 0
        # Get latest line
        for line in text.split('\n'):
            if len(line) < 1:
                continue
            if not check_sync_status(line):
                print("Invalid sync status")
                return False, 0, 0, 0, 0, 0
    # Get the results from the files
    with open(result_file, 'r') as file:
        sections = read_results(file.read())
        # Add [Application] prefix to method name if not present
        method_key = f'[Application] {method}' if not method.startswith('[Application]') else method
        
        if method_key not in sections:
            print(f"Method '{method_key}' not found in sections for file {result_file}. Available methods: {list(sections.keys())}")
            # Get timestamp from first available section, or 0 if no sections exist
            timestamp = getattr(next(iter(sections.values())), 'timestamp', 0) if sections else 0
            return False, 0, timestamp, 0, 0, 0
        result = sections[method_key].fields[field]
        timestamp = getattr(sections[method_key], 'timestamp', 0)
        # Extract total running time if available (in milliseconds)
        total_running_time_ms = 0
        if '[Application] Total Running Time' in sections:
            total_running_time_section = sections['[Application] Total Running Time']
            if 'sum' in total_running_time_section.fields:
                total_running_time_ms = float(total_running_time_section.fields['sum'])
        
        # Extract FCU (engine_forkchoiceUpdatedV3) duration
        fcu_duration_ms = 0
        if '[Application] engine_forkchoiceUpdatedV3' in sections:
            fcu_section = sections['[Application] engine_forkchoiceUpdatedV3']
            if 'sum' in fcu_section.fields:
                fcu_duration_ms = float(fcu_section.fields['sum'])
        
        # Extract NP (engine_newPayloadV4) duration
        np_duration_ms = 0
        if '[Application] engine_newPayloadV4' in sections:
            np_section = sections['[Application] engine_newPayloadV4']
            if 'sum' in np_section.fields:
                np_duration_ms = float(np_section.fields['sum'])
    
    return response, float(result), timestamp, total_running_time_ms, fcu_duration_ms, np_duration_ms


def get_gas_table(client_results, client, test_cases, gas_set, method, metadata):
    gas_table_norm = {}
    results_per_test_case = {}
    for test_case, _ in test_cases.items():
        for gas in gas_set:
            if gas not in client_results[client][test_case]:
                continue
            if test_case not in results_per_test_case:
                results_per_test_case[test_case] = []
            results = client_results[client][test_case][gas][method]
            for x in results:
                if x == 0:
                    continue
                gas_values_for_case = test_cases.get(test_case, {})
                actual_mgas = gas_values_for_case.get(gas, gas)
                if actual_mgas == 0:
                    continue
                results_per_test_case[test_case].append(actual_mgas / x * 1000)

    for test_case, _ in test_cases.items():
        results_norm = results_per_test_case[test_case]
        gas_table_norm[test_case] = ['' for _ in range(13)]
        # test_case_name, description, N, MGgas/s, mean, max, min. std, p50, p95, p99
        # (norm) title, description, N , max, min, p50, p95, p99, start_time, end_time, duration_ms, fcu_duration_ms, np_duration_ms
        timestamp_ticks = client_results[client][test_case]["timestamp_ticks"] if client_results[client][test_case] and "timestamp_ticks" in client_results[client][test_case] else 0
        duration_ms = client_results[client][test_case]["duration"] if client_results[client][test_case] and "duration" in client_results[client][test_case] else 0
        fcu_duration_ms = client_results[client][test_case]["fcu_duration"] if client_results[client][test_case] and "fcu_duration" in client_results[client][test_case] else 0
        np_duration_ms = client_results[client][test_case]["np_duration"] if client_results[client][test_case] and "np_duration" in client_results[client][test_case] else 0
        
        # Convert start timestamp to formatted string
        start_time_str = convert_dotnet_ticks_to_utc(timestamp_ticks) if timestamp_ticks != 0 else 0
        gas_table_norm[test_case][8] = start_time_str
        
        # Calculate end time using raw ticks + duration
        # 1 ms = 10,000 ticks (since 1 tick = 100 nanoseconds)
        if timestamp_ticks != 0 and duration_ms != 0:
            duration_ticks = int(duration_ms * 10_000)
            end_time_ticks = timestamp_ticks + duration_ticks
            end_time_str = convert_dotnet_ticks_to_utc(end_time_ticks)
            gas_table_norm[test_case][9] = end_time_str
        else:
            gas_table_norm[test_case][9] = 0
        
        # Store duration in milliseconds
        gas_table_norm[test_case][10] = f'{duration_ms:.2f}' if duration_ms != 0 else '0'
        
        # Store FCU and NP durations
        gas_table_norm[test_case][11] = f'{fcu_duration_ms:.2f}' if fcu_duration_ms != 0 else '0'
        gas_table_norm[test_case][12] = f'{np_duration_ms:.2f}' if np_duration_ms != 0 else '0'
            
        if test_case in metadata:
            gas_table_norm[test_case][0] = metadata[test_case]['Title']
            gas_table_norm[test_case][7] = metadata[test_case]['Description']
        else:
            gas_table_norm[test_case][0] = test_case
            gas_table_norm[test_case][7] = 'Description not found on metadata file'
        if len(results_norm) == 0:
            gas_table_norm[test_case][1] = f'0'
            gas_table_norm[test_case][2] = f'0'
            gas_table_norm[test_case][3] = f'0'
            gas_table_norm[test_case][4] = f'0'
            gas_table_norm[test_case][5] = f'0'
            gas_table_norm[test_case][6] = f'0'
            continue
        gas_table_norm[test_case][1] = f'{min(results_norm):.2f}'
        gas_table_norm[test_case][2] = f'{max(results_norm):.2f}'
        percentiles = calculate_percentiles(results_norm, [50, 5, 1])
        gas_table_norm[test_case][3] = f'{np.percentile(percentiles[50], 50):.2f}'
        gas_table_norm[test_case][4] = f'{np.percentile(percentiles[5], 5):.2f}'
        gas_table_norm[test_case][5] = f'{np.percentile(percentiles[1], 1):.2f}'
        gas_table_norm[test_case][6] = f'{len(results_norm)}'
    return gas_table_norm


def calculate_percentiles(values, percentiles):
    """
    Calculate the specified percentiles for a list of values where smaller values are better.

    Args:
        values (list): A list of numeric values.
        percentiles (list): A list of percentiles to calculate (e.g., [50, 95, 99]).

    Returns:
        dict: A dictionary containing the calculated percentiles.
    """
    sorted_values = sorted(values)
    n = len(sorted_values)

    result = {}
    for p in percentiles:
        index = (p / 100) * (n + 1) - 1

        if index.is_integer():
            result[p] = sorted_values[int(index)]
        else:
            lower_index = math.floor(index)
            upper_index = min(math.ceil(index), len(sorted_values) - 1)

            lower_value = sorted_values[int(lower_index)]
            upper_value = sorted_values[int(upper_index)]

            fraction = index - lower_index
            result[p] = lower_value + fraction * (upper_value - lower_value)

    return result


def check_sync_status(json_data):
    data = json.loads(json_data)
    if 'result' not in data:
        return False
    if 'status' in data['result']:
        return data['result']['status'] == 'VALID'
    elif 'payloadStatus' in data['result']:
        return data['result']['payloadStatus']['status'] == 'VALID'
    else:
        return False


def check_client_response_is_valid(results_paths, client, test_case, length):
    for i in range(1, length + 1):
        response_file = f'{results_paths}/{client}_response_{i}_{test_case}'
        if not os.path.exists(response_file):
            return False
        with open(response_file, 'r') as file:
            text = file.read()
            if len(text) == 0:
                return False
            # Get latest line
            for line in text.split('\n'):
                if len(line) < 1:
                    continue
                if not check_sync_status(line):
                    return False
    return True


def _extract_opcount_from_name(name: str) -> Optional[int]:
    """
    Parse an opcount suffix such as 'opcount_50000K' or 'opcount_125M' from a test filename.
    Returns the integer count (e.g., 50000000) or None if not present.
    """
    match = re.search(r"opcount_(\d+)([kKmM]?)", name)
    if not match:
        return None

    value = int(match.group(1))
    suffix = match.group(2).lower()
    if suffix == "k":
        value *= 1_000
    elif suffix == "m":
        value *= 1_000_000
    return value


def get_test_cases(tests_path: str, return_metadata: bool = False):
    """
    Discover test cases and derive their gas usage and optional opcount metadata.

    Args:
        tests_path: Root directory containing the test payloads.
        return_metadata: When True, also return a per-variant metadata mapping that includes
                         the exact result file token, gas_used_mgas, and opcount.

    Returns:
        If return_metadata is False (default), a mapping of test_case -> {gas_label: gas_used_mgas}.
        If return_metadata is True, a tuple of (test_cases, metadata) where metadata mirrors
        the same keys as test_cases but the values are dicts with extra fields.
    """
    test_cases: Dict[str, Dict[int, float]] = defaultdict(dict)
    test_metadata: Dict[str, Dict[int, dict]] = defaultdict(dict)
    pattern = re.compile(r'(?P<base>.+?)_(?P<gas>[0-9]+)M\.txt$')

    for root, _, files in os.walk(tests_path):
        normalized_root = root.replace('\\', '/')
        if '/testing/' not in normalized_root and not normalized_root.endswith('/testing'):
            continue

        for file in files:
            if not file.endswith('.txt'):
                continue

            base_name = os.path.splitext(file)[0]
            m = pattern.match(file)
            if m:
                test_case_name = m.group('base')
                gas_label = int(m.group('gas'))  # e.g., "100" from "100M"
            else:
                test_case_name = base_name
                gas_label = 60

            file_path = os.path.join(root, file)
            gas_used_units = _extract_gas_used_from_payload(file_path)
            if gas_used_units is None:
                gas_used_millions = float(gas_label)
            else:
                gas_used_millions = gas_used_units / 1_000_000.0

            opcount = _extract_opcount_from_name(base_name)
            test_cases[test_case_name][gas_label] = gas_used_millions
            test_metadata[test_case_name][gas_label] = {
                "result_token": base_name,
                "opcount": opcount,
                "gas_value_mgas": gas_used_millions,
            }

    # Preserve ordering of gas labels for deterministic output
    sorted_cases = {tc: dict(sorted(gases.items())) for tc, gases in test_cases.items()}
    sorted_meta = {tc: {k: test_metadata[tc][k] for k in sorted(test_metadata[tc])} for tc in test_metadata}

    if return_metadata:
        return sorted_cases, sorted_meta
    return sorted_cases


def _extract_gas_used_from_payload(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                method = data.get("method", "")
                if not isinstance(method, str) or not method.startswith("engine_newPayload"):
                    continue

                params = data.get("params") or []
                if not params or not isinstance(params[0], dict):
                    continue

                gas_used_value = params[0].get("gasUsed")
                if isinstance(gas_used_value, str):
                    gas_used_value = gas_used_value.strip()
                    if not gas_used_value:
                        continue
                    base = 16 if gas_used_value.lower().startswith("0x") else 10
                    return int(gas_used_value, base)
                elif isinstance(gas_used_value, (int, float)):
                    return int(gas_used_value)
    except (OSError, ValueError) as exc:
        logger.warning(f"Unable to parse gasUsed from {file_path}: {exc}")

    return None

class SectionData:
    def __init__(self, timestamp, measurement, tags, fields):
        self.timestamp = timestamp
        self.measurement = measurement
        self.tags = tags
        self.fields = fields

    def __repr__(self):
        return f"SectionData(timestamp={self.timestamp}, measurement='{self.measurement}', tags={self.tags}, " \
               f"fields={self.fields})"


class RPCResponse:
    def __init__(self, jsonrpc, result, id):
        self.jsonrpc = jsonrpc
        self.result = result
        self.id = id

    def __repr__(self):
        return f"RPCResponse(jsonrpc={self.jsonrpc}, result={self.result}, id={self.id})"

    @staticmethod
    def from_dict(data):
        jsonrpc = data.get("jsonrpc")
        result = data.get("result")
        id = data.get("id")
        return RPCResponse(jsonrpc, result, id)

    def get_result_status(self):
        if self.result and "status" in self.result:
            return self.result["status"]
        return None


class PayloadResponse:
    def __init__(self, jsonrpc, result, id):
        self.jsonrpc = jsonrpc
        self.result = result
        self.id = id

    def __repr__(self):
        return f"PayloadResponse(jsonrpc={self.jsonrpc}, result={self.result}, id={self.id})"

    @staticmethod
    def from_dict(data):
        jsonrpc = data.get("jsonrpc")
        result = data.get("result")
        id = data.get("id")
        return PayloadResponse(jsonrpc, result, id)

    def get_payload_status(self):
        if self.result and "payloadStatus" in self.result and "status" in self.result["payloadStatus"]:
            return self.result["payloadStatus"]["status"]
        return None


def print_computer_specs():
    info = "Computer Specs:\n"
    cpu = cpuinfo.get_cpu_info()
    system_info = {
        'Processor': platform.processor(),
        'System': platform.system(),
        'Release': platform.release(),
        'Version': platform.version(),
        'Machine': platform.machine(),
        'Processor Architecture': platform.architecture()[0],
        'RAM': f'{psutil.virtual_memory().total / (1024 ** 3):.2f} GB',
        'CPU': cpu['brand_raw'],
        'Numbers of CPU': cpu['count'],
        'CPU GHz': cpu.get('hz_actual_friendly', 'N/A')
    }

    # Print the specifications
    for key, value in system_info.items():
        line = f'{key}: {value}'
        print(line)
        info += line + "\n"
    return info + "\n"


def merge_csv(first_data, second_data):
    # Take headers from first file, and ignore headers from second file
    headers = first_data[0]

    # Merge the data
    result = [headers]
    result.extend(first_data[1:])
    result.extend(second_data[1:])
    return result


def merge_html(first_data, second_data):
    # Load the HTML data
    first_soup = BeautifulSoup(first_data, 'html.parser')
    second_soup = BeautifulSoup(second_data, 'html.parser')

    # Merge the elements of the tables that has the same id on both HTML files
    for first_table, second_table in zip(first_soup.find_all('table'), second_soup.find_all('table')):
        if first_table['id'] == second_table['id']:
            second_table.find_all('thread')[0].decompose()
            # Only merge the elements of the table, not the table itself, Completely remove from second table thread,
            # will be the same on both files
            for first_element, second_element in zip(first_table.find_all('tr'), second_table.find_all('tr')):
                first_element.append(second_element)


    return first_soup.prettify()

def convert_dotnet_ticks_to_utc(ticks):
    # .NET ticks start at 0001-01-01
    dotnet_epoch = datetime.datetime(1, 1, 1, tzinfo=datetime.timezone.utc)
    # 1 tick = 100 nanoseconds = 0.0000001 seconds
    seconds = ticks / 10_000_000
    dt = dotnet_epoch + datetime.timedelta(seconds=seconds)
    # Format as 'YYYY-MM-DD HH:MI:SS.FFFFFF+00'
    return dt.strftime('%Y-%m-%d %H:%M:%S.%f+00')
