import argparse
import json
import os
import statistics

import utils

# Processed responses will be accessed globally
processed_responses = {}

failed_tests = {}


# get_files will return the files in the following format:
# {'warmup_results': 'file.txt', 'warmup_response': 'file.txt', results': ['file.txt'],
# 'responses': ['file.txt']}
def get_files(results_paths, client, test_case):
    filter_name = test_case.split('/')[-1].split('.')[0]
    # Get all the files in the results folder that match the client
    directory = os.listdir(results_paths)
    files = {
        'warmup_results': None,
        'warmup_response': None,
        'results': [],
        'responses': [],
    }
    for file in directory:
        if file.startswith(f'{client}_response') and filter_name in file:
            files['responses'].append(file)
        elif file.startswith(f'{client}_results') and filter_name in file:
            files['results'].append(file)
        elif file.startswith(f'warmup_{client}_response') and filter_name in file:
            files['warmup_response'] = file
        elif file.startswith(f'warmup_{client}_results') and filter_name in file:
            files['warmup_results'] = file
    return files


def read_responses(text):
    responses = []
    for line in text.split('\n'):
        try:
            if line is None or line == '':
                continue
            data = json.loads(line)
            if "result" in data and isinstance(data["result"], dict) and "payloadStatus" in data["result"]:
                response = utils.PayloadResponse.from_dict(data)
                responses.append(response)
            else:
                response = utils.RPCResponse.from_dict(data)
                responses.append(response)
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON: {e}")
    return responses


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


def extract_data_per_client(client, results_paths, test_case):
    file_names = get_files(results_paths, client, test_case)
    # Get the responses from the files
    responses = {}
    for response in file_names['responses']:
        # parse the name to get the run number
        run = response.split('.')[0].split('_')[2]
        with open(f'{results_paths}/{response}', 'r') as file:
            text = file.read()
            responses[run] = read_responses(text)
    # Get the results from the files
    results = {}
    for result in file_names['results']:
        # parse the name to get the run number
        run = result.split('.')[0].split('_')[2]
        with open(f'{results_paths}/{result}', 'r') as file:
            text = file.read()
            if len(text) == 0:
                failed_tests[client][test_case][run] = True
                continue
            results[run] = read_results(text)
    return responses, results, None, None


# Print graphs and tables with the results
def process_results(client_results, results_paths, method, field, test_case):
    # plt.figure(figsize=(10, 5))
    for client, data in client_results.items():
        results_max = []
        for i in range(1, len(data['results']) + 1):
            try:
                results_max.append(float(data['results'][str(i)][method].fields[field]))
            except KeyError as e:
                results_max.append(0)
                print(f"Error: {e} in {client} {test_case} {method} {field} {i}")

        processed_responses[client][test_case][method][field] = results_max


def standard_deviation(numbers):
    if len(numbers) == 0:
        return None
    return statistics.stdev(numbers)


def center_string(string, size):
    padding_length = max(0, size - len(string))
    padding_left = padding_length // 2
    padding_right = padding_length - padding_left
    centered_string = " " * padding_left + string + " " * padding_right
    return centered_string


def print_processed_responses(results_paths, tests_path, method):
    # Table with results per test case, comparing the clients
    #
    # Test Case 1
    #
    # client/iteration | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | mean
    #           max    | x | x | x | x | x | x | x | x | x |  x |  x
    # client1   min    | x | x | x | x | x | x | x | x | x |  x |  x
    #           mean   | x | x | x | x | x | x | x | x | x |  x |  x
    # -------------------------------------------------------------------
    #           max    | x | x | x | x | x | x | x | x | x |  x |  x
    # client 2  min    | x | x | x | x | x | x | x | x | x |  x |  x
    #           mean   | x | x | x | x | x | x | x | x | x |  x |  x
    # -------------------------------------------------------------------
    #
    # Test Case 2
    #
    # client/iteration | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | mean
    #           max    | x | x | x | x | x | x | x | x | x |  x |  x
    # client1   min    | x | x | x | x | x | x | x | x | x |  x |  x
    #           mean   | x | x | x | x | x | x | x | x | x |  x |  x
    # -------------------------------------------------------------------
    #           max    | x | x | x | x | x | x | x | x | x |  x |  x
    # client 2  min    | x | x | x | x | x | x | x | x | x |  x |  x
    #           mean   | x | x | x | x | x | x | x | x | x |  x |  x
    # -------------------------------------------------------------------
    #
    results = ''
    if os.path.isdir(tests_path):
        for root, _, files in os.walk(tests_path):
            if len(files) == 0:
                continue

            for test_case in files:
                results += f'Test case: {test_case}, request: {method}:\n\n'
                for client in processed_responses.keys():
                    size = 21
                    if test_case not in processed_responses[client]:
                        continue
                    string_centered = center_string('client/iteration', 20)
                    results += f'{string_centered}|'
                    for i in range(1, len(processed_responses[client][test_case][method]['max']) + 1):
                        size += 12
                        results += f'{center_string(str(i), 11)}|'
                    size += 15
                    results += '   stdev\n'
                    i = 0
                    for fields_key in processed_responses[client][test_case][method]:
                        fields = processed_responses[client][test_case][method][fields_key]
                        middle_field = len(processed_responses[client][test_case][method]) // 2
                        if i == middle_field:
                            results += f'{center_string(client, 14)}{center_string(fields_key, 6)}|'
                        else:
                            empty_string = ' ' * 14
                            results += f'{empty_string}{center_string(fields_key, 6)}|'
                        for value in fields:
                            results += center_string(f'{value :.2f} ms', 11) + "|"
                        st_dev = standard_deviation(fields)
                        if st_dev is None:
                            results += center_string(f'N/A', 11)
                        else:
                            st_dev_str = f'{st_dev :.2f} ms'
                            results += f' {center_string(st_dev_str, 11)}\n'
                        i += 1
                    results += ('-' * size) + '\n'
                results += '\n'

    with open(f'{results_paths}/transition_report_{method}.txt', 'w') as file:
        file.write(results)
    print(results)


def main():
    parser = argparse.ArgumentParser(description='Benchmark script')
    parser.add_argument('--resultsPath', type=str, help='Path to gather the results', default='results')
    parser.add_argument('--testsPath', type=str, help='Path to tests cases', default='tests/')
    parser.add_argument('--reportPath', type=str, help='Path to report cases', default='reports')
    parser.add_argument('--clients', type=str, help='Client we want to gather the metrics, if you want to '
                                                    'compare, split them by comma, ex: nethermind,geth',
                        default='nethermind,erigon,geth,reth,besu')

    # Parse command-line arguments
    args = parser.parse_args()

    # Get client name and test case folder from command-line arguments
    results_paths = args.resultsPath
    report_path = args.reportPath
    clients = args.clients
    tests_path = args.testsPath

    # Get the computer spec
    with open(os.path.join(results_paths, 'computer_specs.txt'), 'r') as file:
        text = file.read()
        computer_spec = text
    print(computer_spec)

    client_results = {}
    methods = ['engine_newPayloadV3']
    fields = ['max', 'min', 'mean']
    for client in clients.split(','):
        processed_responses[client] = {}
        failed_tests[client] = {}
        if os.path.isdir(tests_path):
            for root, _, files in os.walk(tests_path):
                if len(files) == 0:
                    continue
                for test_case in files:
                    processed_responses[client][test_case] = {}
                    failed_tests[client][test_case] = {}
                    for method in methods:
                        processed_responses[client][test_case][method] = {}
                        for field in fields:
                            processed_responses[client][test_case][method][field] = []
        else:
            processed_responses[client][tests_path] = {}
            for method in methods:
                processed_responses[client][tests_path][method] = {}
                for field in fields:
                    processed_responses[client][tests_path][method][field] = []

    if os.path.isdir(tests_path):
        for root, _, files in os.walk(tests_path):
            if len(files) == 0:
                continue
            for test_case in files:
                for client in clients.split(','):
                    responses, results, warmup_responses, warmup_results = extract_data_per_client(client,
                                                                                                   results_paths,
                                                                                                   test_case)
                    client_results[client] = {
                        'responses': responses,
                        'results': results,
                        'warmup_responses': warmup_responses,
                        'warmup_results': warmup_results
                    }

                for method in methods:
                    for field in fields:
                        process_results(client_results, results_paths, method, field, test_case)
    else:
        for client in clients.split(','):
            responses, results, warmup_responses, warmup_results = extract_data_per_client(client, results_paths,
                                                                                           tests_path)
            client_results[client] = {
                'responses': responses,
                'results': results,
                'warmup_responses': warmup_responses,
                'warmup_results': warmup_results
            }

        for method in methods:
            for field in fields:
                process_results(client_results, results_paths, method, field, tests_path)

    for method in methods:
        print_processed_responses(report_path, tests_path, method)

    print('Done!')


if __name__ == '__main__':
    main()
