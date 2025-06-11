import json
import math
import os

import cpuinfo
import platform

import numpy as np
import psutil
from bs4 import BeautifulSoup


def read_results(text):
    sections = {}
    for sections_text in text.split('--------------------------------------------------------------'):
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
                measurement = full_lines.split(' ')[3].strip()
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
        print("No result in:", result_file)
        return False, 0
    if not os.path.exists(response_file):
        print("No repsonse")
        return False, 0
    # Get the responses from the files
    with open(response_file, 'r') as file:
        text = file.read()
        if len(text) == 0:
            Print("text len 0")
            return False, 0
        # Get latest line
        for line in text.split('\n'):
            if len(line) < 1:
                continue
            if not check_sync_status(line):
                print("Invalid sync status")
                return False, 0
    # Get the results from the files
    with open(result_file, 'r') as file:
        sections = read_results(file.read())
        if method not in sections:
            print("no method")
            return False, 0
        result = sections[method].fields[field]
    return response, float(result)


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
        gas_table_norm[test_case] = ['' for _ in range(8)]
        # test_case_name, description, N, MGgas/s, mean, max, min. std, p50, p95, p99
        # (norm) title, description, N , max, min, p50, p95, p99
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
    test_cases = {
        # 'test_case_name': ['gas_used']
    }

    tests_cases_list = []
    for root, _, files in os.walk(tests_path):
        if len(files) == 0:
            continue
        for file in files:
            tests_cases_list.append(os.path.join(root, file))
    for test_case in tests_cases_list:
        if test_case.endswith('.txt'):
            test_case_parsed = test_case.split('/')[-1].split('_')
            test_case_name = test_case_parsed[0]
            test_case_gas = test_case_parsed[1].split('M')[0]
            if test_case_name not in test_cases:
                test_cases[test_case_name] = [test_case_gas]
            else:
                test_cases[test_case_name].append(test_case_gas)
    return test_cases

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
