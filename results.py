import argparse
import json
import os

import utils
import matplotlib.pyplot as plt


# get_files will return the files in the following format:
# {'warmup_results': 'file.txt', 'warmup_response': 'file.txt', results': ['file.txt'],
# 'responses': ['file.txt']}
def get_files(results_paths, client):
    # Get all the files in the results folder that match the client
    directory = os.listdir(results_paths)
    files = {
        'warmup_results': None,
        'warmup_response': None,
        'results': [],
        'responses': [],
    }
    for file in directory:
        if file.startswith(f'{client}_response'):
            files['responses'].append(file)
        elif file.startswith(f'{client}_results'):
            files['results'].append(file)
        elif file.startswith(f'warmup_{client}_response'):
            files['warmup_response'] = file
        elif file.startswith(f'warmup_{client}_results'):
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


def extract_data_per_client(client, results_paths):
    file_names = get_files(results_paths, client)
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
    # Get the warmup responses
    with open(f'{results_paths}/{file_names["warmup_response"]}', 'r') as file:
        text = file.read()
        warmup_responses = read_responses(text)
    # Get the warmup results
    with open(f'{results_paths}/{file_names["warmup_results"]}', 'r') as file:
        text = file.read()
        warmup_results = read_results(text)
    return responses, results, warmup_responses, warmup_results


# Print graphs and tables with the results
def process_results(client_results, results_paths, method, field):
    plt.figure(figsize=(10, 5))
    for client, data in client_results.items():
        results_max = []
        for i in range(len(data['results'])):
            results_max.append(float(data['results'][str(i)][method].fields[field]))

        x = range(1, len(data['results']) + 1)
        plt.plot(x, results_max, label=client)
        plt.xticks(list(x)[::1])
    plt.legend()
    plt.title(f'{field} results')
    plt.savefig(f'{results_paths}/{method}_{field}_results.png')
    plt.close()
    pass


def main():
    parser = argparse.ArgumentParser(description='Benchmark script')
    parser.add_argument('--resultsPath', type=str, help='Path to gather the results', default='results')
    parser.add_argument('--clients', type=str, help='Client we want to gather the metrics, if you want to compare, '
                                                    'split them by comma, ex: nethermind,geth', default='nethermind')

    # Parse command-line arguments
    args = parser.parse_args()

    # Get client name and test case folder from command-line arguments
    results_paths = args.resultsPath
    clients = args.clients

    # Get the computer spec
    with open(os.path.join(results_paths, 'computer_specs.txt'), 'r') as file:
        text = file.read()
        computer_spec = text

    client_results = {}
    for client in clients.split(','):
        responses, results, warmup_responses, warmup_results = extract_data_per_client(client, results_paths)
        client_results[client] = {
            'responses': responses,
            'results': results,
            'warmup_responses': warmup_responses,
            'warmup_results': warmup_results
        }

    print(computer_spec)

    for method in ['engine_forkchoiceUpdatedV3', 'engine_newPayloadV3']:
        for field in ['max', 'min', 'mean', 'sum']:
            process_results(client_results, results_paths, method, field)


if __name__ == '__main__':
    main()
