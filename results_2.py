import argparse
import json
import os
import statistics

import numpy as np

import utils
import matplotlib.pyplot as plt


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
            sections[measurement] = utils.SectionData(timestamp, measurement, tags, fields)

    return sections


def check_sync_status(json_data):
    data = json.loads(json_data)
    if 'status' in data['result']:
        return data['result']['status'] == 'VALID'
    elif 'payloadStatus' in data['result']:
        return data['result']['payloadStatus']['status'] == 'VALID'
    else:
        return False


def extract_response_and_result(results_path, client, test_case_name, gas_used, run, method, field):
    result_file = f'{results_path}/{client}_results_{run}_{test_case_name}_{gas_used}M.txt'
    response_file = f'{results_path}/{client}_response_{run}_{test_case_name}_{gas_used}M.txt'
    response = True
    result = 0
    # Get the responses from the files
    with open(response_file, 'r') as file:
        text = file.read()
        if len(text) == 0:
            return False, 0
        # Get latest line
        for line in text.split('\n'):
            if len(line) < 1:
                continue
            if not check_sync_status(line):
                return False, 0
    # Get the results from the files
    with open(result_file, 'r') as file:
        sections = read_results(file.read())
        if method not in sections:
            return False, 0
        result = sections[method].fields[field]
    return response, float(result)


# Print graphs and tables with the results
def process_results(client_results, clients, results_paths, test_cases, failed_tests, methods, metadata, percentiles=False):
    results_to_print = ''
    for test_case, gas_used in test_cases.items():
        for method in methods:
            add_header = ' -- (Percentiles)' if percentiles else ''
            if test_case in metadata:
                title = metadata[test_case]['Title']
                description = metadata[test_case]['Description']
                results_to_print += f'\n\nTitle: {title} -- Description: {description}{add_header}:\n'
            else:
                results_to_print += f'\n\nTitle: {test_case}{add_header}:\n'
            gas_bar = [int(gas) for gas in gas_used]
            gas_bar.sort()
            main_headers = [center_string('client/gas', 20)]
            for gas in gas_bar:
                centered_string = center_string(str(gas) + 'M', 14)
                main_headers.append(centered_string)
            header = '|'.join(main_headers)
            results_to_print += f'{header}\n'
            results_to_print += '-' * (40 + (14 * len(gas_bar))) + '\n'
            # Create a table with the results
            # Table will have the following format:
            # | client/gas  | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
            # |{client} max | x | x | x | x | x | x | x | x | x | x  |
            # |         min | x | x | x | x | x | x | x | x | x | x  |
            # |         avg | x | x | x | x | x | x | x | x | x | x  |
            # |         std | x | x | x | x | x | x | x | x | x | x  |
            plt.figure(figsize=(10, 5))

            for client in clients:
                table = [['' for _ in range(len(gas_bar))] for _ in range(7)]
                for i in range(0, len(gas_bar)):
                    gas = str(gas_bar[i])
                    if True in failed_tests[client][test_case][gas][method]:
                        na = center_string('N/A', 14)
                        for ti in range(len(table)):
                            table[ti][i] = na
                    max_val = max(client_results[client][test_case][gas][method])
                    min_val = f'{min(client_results[client][test_case][gas][method]):.2f} ms'
                    avg_val = f'{sum(client_results[client][test_case][gas][method]) / len(client_results[client][test_case][gas][method]):.2f} ms'
                    std_val = f'{standard_deviation(client_results[client][test_case][gas][method]):.2f}'
                    p50_val = f'{np.percentile(client_results[client][test_case][gas][method], 50):.2f}'
                    p95_val = f'{np.percentile(client_results[client][test_case][gas][method], 95):.2f}'
                    p99_val = f'{np.percentile(client_results[client][test_case][gas][method], 99):.2f}'
                    table[0][i] = max_val
                    table[1][i] = f'{center_string(min_val, 14)}'
                    table[2][i] = f'{center_string(avg_val, 14)}'
                    table[3][i] = f'{center_string(std_val, 14)}'
                    table[4][i] = f'{center_string(p50_val, 14)}'
                    table[5][i] = f'{center_string(p95_val, 14)}'
                    table[6][i] = f'{center_string(p99_val, 14)}'

                if percentiles:
                    p50_row = center_string(f'{client} p50', 20)
                    results_to_print += f'{p50_row}|{"|".join(table[4])}\n'
                    p90_row = center_string('p90', 20)
                    results_to_print += f'{p90_row}|{"|".join(table[5])}\n'
                    p99_row = center_string('p99', 20)
                    results_to_print += f'{p99_row}|{"|".join(table[6])}\n'
                    results_to_print += '-' * (40 + (14 * len(gas_bar))) + '\n'
                else:
                    max_row = center_string(f'{client} max', 20)
                    row = []
                    for item in table[0]:
                        str_item = f'{item:.2f} ms'
                        row.append(f'{center_string(str_item, 14)}')
                    results_to_print += f'{max_row}|{"|".join(row)}\n'

                    min_row = center_string('min', 20)
                    results_to_print += f'{min_row}|{"|".join(table[1])}\n'
                    avg_row = center_string('avg', 20)
                    results_to_print += f'{avg_row}|{"|".join(table[2])}\n'
                    std_row = center_string('std', 20)
                    results_to_print += f'{std_row}|{"|".join(table[3])}\n'
                    results_to_print += '-' * (40 + (14 * len(gas_bar))) + '\n'
                # x = range(1, len(gas_bar) + 1)
                plt.plot(gas_bar, [float(x) for x in table[0]], label=client)
                # plt.xticks(lis)

            plt.legend()
            plt.title(f'Max results')
            if not os.path.exists(f'{results_paths}/charts'):
                os.makedirs(f'{results_paths}/charts')
            plt.savefig(f'{results_paths}/charts/{test_case}_{method}_results.png')
            plt.close()

            results_to_print += '\n\n'

    print(results_to_print)
    percentiles_file_name = '_percentiles' if percentiles else ''
    with open(f'{results_paths}/reports/tables{percentiles_file_name}.txt', 'w') as file:
        file.write(results_to_print)


# Print graphs and tables with the results
def get_gas_table(client_results, client, test_cases, gas, method, metadata):
    gas_table = {}
    for test_case, _ in test_cases.items():
        if gas not in client_results[client][test_case]:
            continue
        results = client_results[client][test_case][gas][method]
        gas_table[test_case] = ['' for _ in range(11)]
        # test_case_name, description, N, MGgas/s, mean, max, min. std, p50, p95, p99
        max_val = max(results)
        if test_case in metadata:
            gas_table[test_case][0] = metadata[test_case]['Title']
            gas_table[test_case][1] = metadata[test_case]['Description']
        else:
            gas_table[test_case][0] = test_case
            gas_table[test_case][1] = 'Description not found in metadata'
        gas_table[test_case][2] = f'{len(results)}'
        gas_table[test_case][3] = f'{int(gas) / max_val:.2f} g/s'
        gas_table[test_case][4] = f'{sum(results) / len(results):.2f} ms'
        gas_table[test_case][5] = f'{max_val:.2f} ms'
        gas_table[test_case][6] = f'{min(results):.2f} ms'
        gas_table[test_case][7] = f'{standard_deviation(results):.2f}'
        gas_table[test_case][8] = f'{np.percentile(results, 50):.2f}'
        gas_table[test_case][9] = f'{np.percentile(results, 95):.2f}'
        gas_table[test_case][10] = f'{np.percentile(results, 99):.2f}'

    return gas_table


def get_gas_table_2(client_results, client, test_cases, gas_set, method, metadata):
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
        gas_table_norm[test_case][1] = f'{max(results_norm):.2f}'
        gas_table_norm[test_case][2] = f'{min(results_norm):.2f}'
        gas_table_norm[test_case][3] = f'{np.percentile(results_norm, 50):.2f}'
        gas_table_norm[test_case][4] = f'{np.percentile(results_norm, 95):.2f}'
        gas_table_norm[test_case][5] = f'{np.percentile(results_norm, 99):.2f}'
        gas_table_norm[test_case][6] = f'{len(results_norm)}'

    return gas_table_norm


def get_gas_resume(client_results, client, test_cases, gas_set, method, metadata):
    gas_table_norm = {}
    result_table = {}
    for gas in gas_set:
        gas_table_norm[gas] = {}
        result_table[gas] = {}
    for test_case, _ in test_cases.items():
        for gas in gas_set:
            if gas not in client_results[client][test_case]:
                continue
            results = [int(gas) / x * 1000 for x in client_results[client][test_case][gas][method]]
            gas_table_norm[gas][test_case] = max(results)

    for gas in gas_set:
        test_case_max_title = ''
        test_case_max_val = 0.0
        for test_case, val in gas_table_norm[gas].items():
            if val > test_case_max_val:
                if test_case in metadata:
                    test_case_max_title = metadata[test_case]['Title']
                    test_case_max_val = val
                else:
                    test_case_max_title = test_case
                    test_case_max_val = val
        result_table[gas] = [test_case_max_title, test_case_max_val]

    return result_table


def process_results_2(client_results, clients, results_paths, test_cases, failed_tests, methods, gas_set,
                      metadata, percentiles=False):
    results_to_print = ''

    for gas in gas_set:

        for client in clients:
            results_to_print += f'{client.capitalize()} Performance Report with {gas}M gas' + '\n'
            results_to_print += (center_string('Title', 55) + '|   '
                                                              'MGgas/s  |    '
                                                              'mean    |     '
                                                              'max    |     '
                                                              'min    |    std '
                                                              '  |    p50   |  '
                                                              '  p95   |    '
                                                              'p99   |  N   | ' + center_string('Description',
                                                                                                  50) + '\n')
            gas_table = get_gas_table(client_results, client, test_cases, gas, methods[0], metadata)
            for test_case, data in gas_table.items():
                results_to_print += (f'{align_left_string(data[0], 55)}|'
                                     f'{center_string(data[3], 12)}|'
                                     f'{center_string(data[4], 12)}|'
                                     f'{center_string(data[5], 12)}|'
                                     f'{center_string(data[6], 12)}|'
                                     f'{center_string(data[7], 10)}|'
                                     f'{center_string(data[8], 10)}|'
                                     f'{center_string(data[9], 10)}|'
                                     f'{center_string(data[10], 10)}|'
                                     f'{center_string(data[2], 6)}|'
                                     f' {align_left_string(data[1], 50)}\n')
            results_to_print += '\n\n'

    print(results_to_print)
    with open(f'{results_paths}/reports/tables_report.txt', 'w') as file:
        file.write(results_to_print)


def process_results_3(client_results, clients, results_paths, test_cases, methods, gas_set, metadata):
    results_to_print = ''

    for client in clients:
        results_to_print += f'{client.capitalize()} Benchmarking Report' + '\n'
        results_to_print += (center_string('Title',
                                           55) + '| Max (MGas/s) | Min (MGas/s) | p50 (MGas/s) | p95 (MGas/s) | p99 (MGas/s) |   N   |    Description\n')
        gas_table_norm = get_gas_table_2(client_results, client, test_cases, gas_set, methods[0], metadata)
        for test_case, data in gas_table_norm.items():
            results_to_print += (f'{align_left_string(data[0], 55)}|'
                                 f'{center_string(data[1], 14)}|'
                                 f'{center_string(data[2], 14)}|'
                                 f'{center_string(data[3], 14)}|'
                                 f'{center_string(data[4], 14)}|'
                                 f'{center_string(data[5], 14)}|'
                                 f'{center_string(data[6], 7)}|'
                                 f' {align_left_string(data[7], 50)}\n')
        results_to_print += '\n'

        resume = get_gas_resume(client_results, client, test_cases, gas_set, methods[0], metadata)
        results_to_print += 'Worst Test Cases\n'

        gas_to_int = [int(x) for x in gas_set]
        for gas in sorted(gas_to_int):
            gas_name = f'{gas}M'
            results_to_print += f'{align_left_string(gas_name, 6)}: {align_left_string(resume[str(gas)][0], 45)}, {resume[str(gas)][1]:.2f} MGas/s\n'
        results_to_print += '\n\n'

    print(results_to_print)
    with open(f'{results_paths}/reports/tables_norm.txt', 'w') as file:
        file.write(results_to_print)


def standard_deviation(numbers):
    if len(numbers) < 2:
        return None
    return statistics.stdev(numbers)


def center_string(string, size):
    padding_length = max(0, size - len(string))
    padding_left = padding_length // 2
    padding_right = padding_length - padding_left
    centered_string = " " * padding_left + string + " " * padding_right
    return centered_string


def align_left_string(string, size):
    padding_right = max(0, size - len(string))
    centered_string = string + " " * padding_right
    return centered_string


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


def main():
    parser = argparse.ArgumentParser(description='Benchmark script')
    parser.add_argument('--resultsPath', type=str, help='Path to gather the results', default='results')
    parser.add_argument('--testsPath', type=str, help='resultsPath', default='tests/SStore')
    parser.add_argument('--clients', type=str, help='Client we want to gather the metrics, if you want to compare, '
                                                    'split them by comma, ex: nethermind,geth,reth',
                        default='nethermind,geth,reth')
    parser.add_argument('--runs', type=int, help='Number of runs the program will process', default='6')

    # Parse command-line arguments
    args = parser.parse_args()

    # Get client name and test case folder from command-line arguments
    results_paths = args.resultsPath
    clients = args.clients
    tests_path = args.testsPath
    runs = args.runs

    # Get the computer spec
    with open(os.path.join(results_paths, 'computer_specs.txt'), 'r') as file:
        text = file.read()
        computer_spec = text
    print(computer_spec)

    client_results = {}
    failed_tests = {}
    methods = ['engine_newPayloadV3']
    fields = 'max'

    test_cases = get_test_cases(tests_path)
    for client in clients.split(','):
        client_results[client] = {}
        failed_tests[client] = {}
        for test_case_name, test_case_gas in test_cases.items():
            client_results[client][test_case_name] = {}
            failed_tests[client][test_case_name] = {}
            for gas in test_case_gas:
                client_results[client][test_case_name][gas] = {}
                failed_tests[client][test_case_name][gas] = {}
                for method in methods:
                    client_results[client][test_case_name][gas][method] = []
                    failed_tests[client][test_case_name][gas][method] = []
                    for run in range(1, runs + 1):
                        responses, results = extract_response_and_result(results_paths, client, test_case_name, gas,
                                                                         run, method, fields)
                        client_results[client][test_case_name][gas][method].append(results)
                        failed_tests[client][test_case_name][gas][method].append(not responses)
    #
    gas_set = set()
    for test_case_name, test_case_gas in test_cases.items():
        for gas in test_case_gas:
            if gas not in gas_set:
                gas_set.add(gas)

    if not os.path.exists(f'{results_paths}/reports'):
        os.makedirs(f'{results_paths}/reports')

    metadata = {}
    if os.path.exists(f'{tests_path}/metadata.json'):
        data = json.load(open(f'{tests_path}/metadata.json', 'r'))
        for item in data:
            metadata[item['Name']] = item

    # process_results_2(client_results, clients.split(','), results_paths, test_cases, failed_tests, methods, gas_set,
    #                   metadata)
    # process_results_3(client_results, clients.split(','), results_paths, test_cases, methods, gas_set, metadata)

    # Print results without percentiles
    process_results(client_results, clients.split(','), results_paths, test_cases, failed_tests, methods, metadata, False)
    # Print results with percentiles
    process_results(client_results, clients.split(','), results_paths, test_cases, failed_tests, methods, metadata, True)

    print('Done!')


if __name__ == '__main__':
    main()
