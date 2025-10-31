import json
import math
import os
import re
from collections import defaultdict

import cpuinfo
import platform

import numpy as np
import psutil
from bs4 import BeautifulSoup
import datetime

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


def extract_response_and_result(results_path, client, test_case_name, gas_used, run, method, field):
    result_file = f'{results_path}/{client}_results_{run}_{test_case_name}_{gas_used}M.txt'
    response_file = f'{results_path}/{client}_response_{run}_{test_case_name}_{gas_used}M.txt'
    response = True
    result = 0
    if not os.path.exists(result_file):
        # print("No result: " + result_file)
        print("No result")
        return False, 0, 0, 0
    if not os.path.exists(response_file):
        print("No repsonse")
        return False, 0, 0, 0
    # Get the responses from the files
    with open(response_file, 'r') as file:
        text = file.read()
        if len(text) == 0:
            print("text len 0")
            return False, 0, 0, 0
        # Get latest line
        for line in text.split('\n'):
            if len(line) < 1:
                continue
            if not check_sync_status(line):
                print("Invalid sync status")
                return False, 0, 0, 0
    # Get the results from the files
    with open(result_file, 'r') as file:
        sections = read_results(file.read())
        if method not in sections:
            print(f"Method '{method}' not found in sections for file {result_file}. Available methods: {list(sections.keys())}")
            # Get timestamp from first available section, or 0 if no sections exist
            timestamp = getattr(next(iter(sections.values())), 'timestamp', 0) if sections else 0
            return False, 0, timestamp, 0
        result = sections[method].fields[field]
        timestamp = getattr(sections[method], 'timestamp', 0)
        # Extract total running time if available (in milliseconds)
        total_running_time_ms = 0
        if '[Application] Total Running Time' in sections:
            total_running_time_section = sections['[Application] Total Running Time']
            if 'sum' in total_running_time_section.fields:
                total_running_time_ms = float(total_running_time_section.fields['sum'])
                print(f"DEBUG EXTRACT: file={result_file}, duration={total_running_time_ms}")
            else:
                print(f"DEBUG EXTRACT: No 'sum' in Total Running Time for {result_file}")
        else:
            print(f"DEBUG EXTRACT: No Total Running Time section in {result_file}")
    return response, float(result), timestamp, total_running_time_ms


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
                results_per_test_case[test_case].append(int(gas) / x * 1000)

    for test_case, _ in test_cases.items():
        results_norm = results_per_test_case[test_case]
        gas_table_norm[test_case] = ['' for _ in range(10)]
        # test_case_name, description, N, MGgas/s, mean, max, min. std, p50, p95, p99
        # (norm) title, description, N , max, min, p50, p95, p99, start_time, end_time
        timestamp_ticks = client_results[client][test_case]["timestamp_ticks"] if client_results[client][test_case] and "timestamp_ticks" in client_results[client][test_case] else 0
        duration_ms = client_results[client][test_case]["duration"] if client_results[client][test_case] and "duration" in client_results[client][test_case] else 0
        
        # Convert start timestamp to formatted string
        start_time_str = convert_dotnet_ticks_to_utc(timestamp_ticks) if timestamp_ticks != 0 else 0
        gas_table_norm[test_case][8] = start_time_str
        
        # Calculate end time using raw ticks + duration
        # 1 ms = 10,000 ticks (since 1 tick = 100 nanoseconds)
        if timestamp_ticks != 0 and duration_ms != 0:
            duration_ticks = int(duration_ms * 10_000)
            end_time_ticks = timestamp_ticks + duration_ticks
            end_time_str = convert_dotnet_ticks_to_utc(end_time_ticks)
            print(f"DEBUG CALC: {test_case} - ticks={timestamp_ticks}, duration_ms={duration_ms}, end={end_time_str}")
            gas_table_norm[test_case][9] = end_time_str
        else:
            print(f"DEBUG CALC: {test_case} - ticks={timestamp_ticks}, duration={duration_ms} - setting to 0")
            gas_table_norm[test_case][9] = 0
            
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


def get_test_cases(tests_path):
    test_cases = defaultdict(set)
    pattern = re.compile(r'(?P<base>.+?)_(?P<gas>[0-9]+)M\.txt$')

    for root, _, files in os.walk(tests_path):
        normalized_root = root.replace('\\', '/')
        if '/testing/' not in normalized_root and not normalized_root.endswith('/testing'):
            continue

        for file in files:
            if not file.endswith('.txt'):
                continue

            m = pattern.match(file)
            if m:
                test_case_name = m.group('base')
                gas_value = int(m.group('gas'))  # e.g., "100" from "100M"
            else:
                test_case_name = os.path.splitext(file)[0]
                gas_value = 60

            test_cases[test_case_name].add(gas_value)

    return {tc: sorted(list(gases)) for tc, gases in test_cases.items()}

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
