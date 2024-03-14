# Create argument parser
import argparse
import datetime
import json
import os
import statistics
import matplotlib.pyplot as plt

import subprocess

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
    print(output)
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


def print_results(client, results, output_folder, gen_charts):
    for test_name, values in results.items():
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
        print(f"Client {client}, Test Name: {test_name}, Ran {len(values)} times")
        print(f"Mean Value: {mean_value:.2f} ms")
        print(f"Median Value: {median_value} ms")
        print(f"Mode Value: {mode_value} ms")
        print(f"Standard Deviation: {std_deviation:.2f} ms")
        print(f"Value Range: {value_range} ms")
        print()

    # Save the results to a JSON file
    current_timestamp = datetime.datetime.now().timestamp()
    output_path = os.path.join(output_folder, f"{client}_results_{int(current_timestamp)}.json")
    with open(output_path, "w") as file:
        json.dump(results, file, indent=4)


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

    # Iterate over the runs
    for i in range(0, number_of_runs):
        # Check if the provided input is a file ending in .json
        if os.path.isfile(tests_paths) and tests_paths.endswith('.json'):
            run = run_command(client_name, tests_paths, repo_path)
            output = process_output(client_name, run)
            if output['name'] not in results:
                results[output['name']] = [output['timeInMs']]
            else:
                results[output['name']].append(output['timeInMs'])
        else:
            # Iterate over files in the specified folder
            for file_name in os.listdir(tests_paths):
                if file_name.endswith('.json'):
                    file_path = os.path.join(tests_paths, file_name)
                    try:
                        run = run_command(client_name, file_path, repo_path)
                        output = process_output(client_name, run)
                        if output['name'] not in results:
                            results[output['name']] = [output['timeInMs']]
                        else:
                            results[output['name']].append(output['timeInMs'])
                    except:
                        print(f"Error processing tests case {file_name}")

    # Create the output folder if it doesn't exist
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # Print results after getting them.
    print_results(client_name, results, output_folder, gen_charts)



if __name__ == '__main__':
    main()
