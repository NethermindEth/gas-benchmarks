import argparse
import json
import os

import yaml

import utils


def get_table_report(client_results, clients, results_paths, test_cases, methods, gas_set, metadata, images):
    results_to_print = ''

    for client in clients:
        image_to_print = ''
        image_json = json.loads(images)
        if client in image_json:
            if image_json[client] != 'default' and image_json[client] != '':
                image_to_print = image_json[client]
        if image_to_print == '':
            with open('images.yaml', 'r') as f:
                el_images = yaml.safe_load(f)["images"]
            client_without_tag = client.split("_")[0]
            image_to_print = el_images[client_without_tag]
        results_to_print += f'{client.capitalize()} - {image_to_print} - Benchmarking Report' + '\n'
        results_to_print += (center_string('Title',
                                           68) + '| Min (MGas/s) | Max (MGas/s) | p50 (MGas/s) | p95 (MGas/s) | p99 (MGas/s) |   N   |    Description | Start time\n')
        gas_table_norm = utils.get_gas_table(client_results, client, test_cases, gas_set, methods[0], metadata)
        for test_case, data in gas_table_norm.items():
            results_to_print += (f'{align_left_string(data[0], 68)}|'
                                 f'{center_string(data[1], 14)}|'
                                 f'{center_string(data[2], 14)}|'
                                 f'{center_string(data[3], 14)}|'
                                 f'{center_string(data[4], 14)}|'
                                 f'{center_string(data[5], 14)}|'
                                 f'{center_string(data[6], 7)}|'
                                 f'{align_left_string(data[7], 50)}|'
                                 f'{data[8]}\n')
        results_to_print += '\n'

    print(results_to_print)
    if not os.path.exists('reports'):
        os.mkdir('reports')
    with open(f'reports/tables_norm.txt', 'w') as file:
        file.write(results_to_print)


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


def main():
    parser = argparse.ArgumentParser(description='Benchmark script')
    parser.add_argument('--resultsPath', type=str, help='Path to gather the results', default='results')
    parser.add_argument('--testsPath', type=str, help='results', default='tests/')
    parser.add_argument('--clients', type=str, help='Client we want to gather the metrics, if you want to compare, '
                                                    'split them by comma, ex: nethermind,geth',
                        default='nethermind,geth,reth')
    parser.add_argument('--runs', type=int, help='Number of runs the program will process', default='10')
    parser.add_argument('--images', type=str, help='Image values per each client',
                        default='{ "nethermind": "default", "besu": "default", "geth": "default", "reth": "default" , '
                                '"erigon": "default"}')

    # Parse command-line arguments
    args = parser.parse_args()

    # Get client name and test case folder from command-line arguments
    results_paths = args.resultsPath
    clients = args.clients
    tests_path = args.testsPath
    runs = args.runs
    images = args.images

    # Get the computer spec
    with open(os.path.join(results_paths, 'computer_specs.txt'), 'r') as file:
        text = file.read()
        computer_spec = text
    print(computer_spec)

    client_results = {}
    failed_tests = {}
    methods = ['engine_newPayloadV3']
    fields = 'max'

    test_cases = utils.get_test_cases(tests_path)
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
                        responses, results, timestamp = utils.extract_response_and_result(results_paths, client, test_case_name,
                                                                               gas, run, method, fields)
                        client_results[client][test_case_name][gas][method].append(results)
                        failed_tests[client][test_case_name][gas][method].append(not responses)
                        # print(test_case_name + " : " + str(timestamp))
                        if str(timestamp) != "0":
                            client_results[client][test_case_name]["timestamp"] = utils.convert_dotnet_ticks_to_utc(timestamp)
                        else:
                            if "timestamp" not in client_results[client][test_case_name]:
                                client_results[client][test_case_name]["timestamp"] = 0


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

    get_table_report(client_results, clients.split(','), results_paths, test_cases, methods, gas_set, metadata, images)
    get_table_report(failed_tests, clients.split(','), results_paths, test_cases, methods, gas_set, metadata, images)

    print('Done!')


if __name__ == '__main__':
    main()
