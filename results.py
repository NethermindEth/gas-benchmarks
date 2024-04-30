import argparse
import json
import os

import utils
import matplotlib.pyplot as plt

# Processed responses will be accessed globally
processed_responses = {}


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
            results[run] = read_results(text)
    # # Get the warmup responses
    # with open(f'{results_paths}/{file_names["warmup_response"]}', 'r') as file:
    #     text = file.read()
    #     warmup_responses = read_responses(text)
    # # Get the warmup results
    # with open(f'{results_paths}/{file_names["warmup_results"]}', 'r') as file:
    #     text = file.read()
    #     warmup_results = read_results(text)
    return responses, results, None, None  # warmup_responses, warmup_results


# Print graphs and tables with the results
def process_results(client_results, results_paths, method, field, test_case):
    plt.figure(figsize=(10, 5))
    for client, data in client_results.items():
        # processed_responses[client]['test_case'] = test_case
        results_max = []
        for i in range(1, len(data['results']) + 1):
            results_max.append(float(data['results'][str(i)][method].fields[field]))

        x = range(1, len(data['results']) + 1)
        processed_responses[client][test_case][method][field] = results_max
        plt.plot(x, results_max, label=client)
        plt.xticks(list(x)[::1])
    plt.legend()
    plt.title(f'{field} results')
    test_name = test_case.split('/')[-1].split('.')[0]
    plt.savefig(f'{results_paths}/{method}_{field}_{test_name}_results.png')
    plt.close()
    pass


def print_processed_responses(results_paths):
    results = ''
    for client, tests_results in processed_responses.items():
        results += f'{client}:\n'
        for test_case, methods in tests_results.items():
            results += f'\t{test_case}:\n'
            for method, fields in methods.items():
                results += f'\t\t{method}:\n'
                for field, values in fields.items():
                    results += f'\t\t\t{field}: {values}\n'

    with open(f'{results_paths}/processed_responses.txt', 'w') as file:
        file.write(results)
    print(results)


def main():
    parser = argparse.ArgumentParser(description='Benchmark script')
    parser.add_argument('--resultsPath', type=str, help='Path to gather the results', default='results')
    parser.add_argument('--testsPath', type=str, help='resultsPath', default='small_tests/1B.txt')
    parser.add_argument('--clients', type=str, help='Client we want to gather the metrics, if you want to compare, '
                                                    'split them by comma, ex: nethermind,geth',
                        default='nethermind,erigon,geth,reth')

    # Parse command-line arguments
    args = parser.parse_args()

    # Get client name and test case folder from command-line arguments
    results_paths = args.resultsPath
    clients = args.clients
    tests_path = args.testsPath

    # Get the computer spec
    with open(os.path.join(results_paths, 'computer_specs.txt'), 'r') as file:
        text = file.read()
        computer_spec = text
    print(computer_spec)

    client_results = {}
    methods = ['engine_forkchoiceUpdatedV3', 'engine_newPayloadV3']
    fields = ['max', 'min', 'mean', 'sum']
    for client in clients.split(','):
        processed_responses[client] = {}
        if os.path.isdir(tests_path):
            for test_case in os.listdir(tests_path):
                processed_responses[client][test_case] = {}
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
        for test_case in os.listdir(tests_path):

            for client in clients.split(','):
                responses, results, warmup_responses, warmup_results = extract_data_per_client(client, results_paths,
                                                                                               test_case)
                client_results[client] = {
                    'responses': responses,
                    'results': results,
                    'warmup_responses': warmup_responses,
                    'warmup_results': warmup_results
                }

            for method in ['engine_forkchoiceUpdatedV3', 'engine_newPayloadV3']:
                for field in ['max', 'min', 'mean', 'sum']:
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

    print_processed_responses(results_paths)

    print('Done!')


if __name__ == '__main__':
    main()
