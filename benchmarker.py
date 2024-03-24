# Create argument parser
import argparse
import datetime
import json
import os
import statistics
import matplotlib.pyplot as plt
import platform
import cpuinfo
import subprocess

import psutil

executables = {
    'dotnet': 'dotnet',
    'go': 'go',
    'cargo': 'cargo',
}


def run_command(client, test_case_file, override_repo_path):
    # Add logic here to run the appropriate command for each client
    if client == 'geth':
        # Run command for geth client
        command = f'geth_command --test-dir {test_case_file}'
    elif client == 'nethermind':
        dotnet = executables['dotnet']
        # Run command for nethermind client
        command = f'cd {client if override_repo_path is None else override_repo_path}/src/Nethermind/Nethermind.Test' \
                  f'.Runner && {dotnet} run --project Nethermind.Test.Runner.csproj ' \
                  f'--configuration Release -- -i {test_case_file} '
    elif client == 'reth':
        # Run command for reth client
        command = f'reth_command --test-dir {test_case_file}'
    else:
        print(f"Unknown client: {client}")
        return

    print(f"Running for {client} client with test '{test_case_file}'")
    results = subprocess.run(command, shell=True, capture_output=True, text=True)
    return results.stdout


def process_geth(output):
    pass


def process_nethermind(output):
    parsed_output = json.loads(output)

    # Get the last element from the list
    last_element = parsed_output[-1]

    # Extract the desired fields from the last element
    name = last_element['name']
    is_pass = last_element['pass']
    fork = last_element['fork']
    time_in_ms = last_element['timeInMs']
    state_root = last_element['stateRoot']

    # Create a struct or dictionary to store the parsed values
    parsed_struct = {
        'name': name,
        'pass': is_pass,
        'fork': fork,
        'timeInMs': time_in_ms,
        'stateRoot': state_root
    }

    return {'name': parsed_struct['name'], 'timeInMs': parsed_struct['timeInMs']}


def process_reth(output):
    pass


# Items will be a dictionary with the following elements:
# {
#   'name':     name,
#   'timeInMs': timeInMs
# }
def process_output(client, output):
    if client == 'geth':
        return process_geth(output)
    elif client == 'nethermind':
        return process_nethermind(output)
    elif client == 'reth':
        return process_reth(output)
    return output


def print_partial_results(client, test_name, values, output_folder, gen_charts):
    # Doesn't makes sense to run the metrics for only one result
    if len(values) < 2:
        return ""

    # Convert the list of string values to a list of floats
    float_values = [float(value) for value in values]

    # Calculate the mean, median, mode, standard deviation, and range
    mean_value = statistics.mean(float_values)
    median_value = statistics.median(float_values)
    mode_value = statistics.mode(float_values)
    std_deviation = statistics.stdev(float_values)
    value_range = max(float_values) - min(float_values)

    if gen_charts:
        # Plot a histogram
        x = range(1, len(values) + 1)
        plt.plot(x, float_values)
        # plt.hist(float_values, bins=10)
        plt.title(f"Plot of {test_name}")
        plt.xlabel("Values")
        plt.ylabel("Frequency")

        plt.xticks(list(x)[::1])

        # Save the plot to the output folder
        output_path = os.path.join(output_folder, f"{test_name}_plot.png")
        plt.savefig(output_path)
        plt.close()

    # Print the metrics
    result_string = ""

    result_string += f"Client {client}, Test Name: {test_name}, Ran {len(values)} times\n"
    result_string += f"Mean Value: {mean_value:.2f} ms\n"
    result_string += f"Median Value: {median_value} ms\n"
    result_string += f"Mode Value: {mode_value} ms\n"
    result_string += f"Standard Deviation: {std_deviation:.2f} ms\n"
    result_string += f"Value Range: {value_range} ms\n\n"

    print(result_string)
    return result_string


def print_final_results(client, results, output_folder, partials_results):
    # Save the results to a JSON file
    current_timestamp = datetime.datetime.now().timestamp()
    output_path = os.path.join(output_folder, f"{client}_results_{int(current_timestamp)}.json")
    with open(output_path, "w") as file:
        json.dump(results, file, indent=4)
    output_path_partials = os.path.join(output_folder, f"{client}_partials_results_{int(current_timestamp)}.txt")
    with open(output_path_partials, "w") as file:
        file.write(partials_results)


def print_computer_specs():
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
        'CPU GHz': cpu['hz_actual_friendly']
    }

    # Print the specifications
    for key, value in system_info.items():
        print(f'{key}: {value}')


def main():
    # get the current working directory
    current_working_directory = os.getcwd()

    # print output to the console
    print(current_working_directory)
    parser = argparse.ArgumentParser(description='Benchmark script')
    parser.add_argument('--client', type=str, help='Client name')
    parser.add_argument('--testsPath', type=str, help='Path to test case folder')
    parser.add_argument('--repoPath', type=str, help='Path to the repo of the client you want to test')
    parser.add_argument('--charts', type=bool, help='If set to true will generate under a folder the graphs generated '
                                                    'for each test', default=True)
    parser.add_argument('--output', type=str, help='Output folder for metrics charts generation. If the folder does '
                                                   'not exist will be created.',
                        default='results')
    parser.add_argument('--numberOfRuns', type=int, help='Number of Runs of the benchmark', default=1)

    # Executables path
    parser.add_argument('--dotnetPath', type=str, help='Path to dotnet executable, needed if testing nethermind and '
                                                       'you need to use something different to dotnet.',
                        default='dotnet')
    parser.add_argument('--goPath', type=str, help='Path to golang executable, needed if testing go-ethereum and '
                                                   'you need to use something different to golang.', default='go')
    parser.add_argument('--cargoPath', type=str, help='Path to cargo executable, needed if testing reth and '
                                                      'you need to use something different to cargo.', default='cargo')

    # Parse command-line arguments
    args = parser.parse_args()

    # Get client name and test case folder from command-line arguments
    client_name = args.client
    tests_paths = args.testsPath
    number_of_runs = args.numberOfRuns
    output_folder = args.output
    gen_charts = args.charts
    repo_path = args.repoPath
    executables['dotnet'] = args.dotnetPath
    executables['go'] = args.goPath
    executables['cargo'] = args.cargoPath

    results = {}
    partial_results = ""
    # Print Computer specs
    print_computer_specs()

    # Check if the provided input is a file ending in .json
    if os.path.isfile(tests_paths) and tests_paths.endswith('.json'):
        # Iterate over the runs
        for i in range(0, number_of_runs):
            run = run_command(client_name, tests_paths, repo_path)
            output = process_output(client_name, run)
            if tests_paths not in results:
                results[tests_paths] = [output['timeInMs']]
            else:
                results[tests_paths].append(output['timeInMs'])
        if tests_paths in results:
            partial_results += print_partial_results(client_name, tests_paths, results[tests_paths], output_folder,
                                                 gen_charts)
    else:
        # Iterate over files in the specified folder
        for file_name in os.listdir(tests_paths):
            if not file_name.endswith('.json'):
                continue
            # Iterate over the runs
            for i in range(0, number_of_runs):
                file_path = os.path.join(tests_paths, file_name)
                try:
                    run = run_command(client_name, file_path, repo_path)
                    output = process_output(client_name, run)
                    if file_name not in results:
                        results[file_name] = [output['timeInMs']]
                    else:
                        results[file_name].append(output['timeInMs'])
                except:
                    print(f"Error processing tests case {file_name}")
            if tests_paths in results:
                partial_results += print_partial_results(client_name, file_name, results[file_name], output_folder,
                                                     gen_charts)

    # Create the output folder if it doesn't exist
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # Print results after getting them.
    print_final_results(client_name, results, output_folder, partial_results)


if __name__ == '__main__':
    main()
