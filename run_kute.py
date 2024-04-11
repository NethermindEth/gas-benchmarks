# Create argument parser
import argparse
import datetime
import json
import os
import subprocess

from utils import print_computer_specs

executables = {
    'kute': './nethermind/tools/Nethermind.Tools.Kute/bin/Release/net8.0/Nethermind.Tools.Kute'
}


def run_command(test_case_file, jwt_secret, response, ec_url, kute_extra_arguments):
    # Add logic here to run the appropriate command for each client
    command = f'{executables["kute"]} -i {test_case_file} -s {jwt_secret} -r {response} -a {ec_url} ' \
              f'{kute_extra_arguments} '
    print(f"Running Kute on client running at url '{ec_url}', with command: '{command}'")
    subprocess.run(command, shell=True, text=True)


def print_final_results(response_path, partial_results, processed_output):
    print(f"Printing results from {response_path}: \n")
    print(partial_results)
    print("Processed Output")
    print(processed_output)
    # Save the results to a JSON file
    # current_timestamp = datetime.datetime.now().timestamp()
    # output_path = os.path.join(output_folder, f"{client}_results_{int(current_timestamp)}.json")
    # with open(output_path, "w") as file:
    #     json.dump(results, file, indent=4)
    # output_path_partials = os.path.join(output_folder, f"{client}_partials_results_{int(current_timestamp)}.txt")
    # with open(output_path_partials, "w") as file:
    #     file.write(partials_results)


def process_output(response_path):
    return ''


def main():
    parser = argparse.ArgumentParser(description='Benchmark script')
    parser.add_argument('--testsPath', type=str, help='Path to test case folder')
    parser.add_argument('--jwtPath', type=str,
                        help='Path to the JWT secret used to communicate with the client you want to test')
    parser.add_argument('--responseFile', type=str, help='If set charts will not be generated', default='response.txt')
    parser.add_argument('--output', type=str, help='Output folder for metrics charts generation. If the folder does '
                                                   'not exist will be created.',
                        default='results')
    # Executables path
    parser.add_argument('--dotnetPath', type=str, help='Path to dotnet executable, needed if testing nethermind and '
                                                       'you need to use something different to dotnet.',
                        default='dotnet')
    parser.add_argument('--kutePath', type=str, help='Path to kute executable.',
                        default='./tools/Nethermind.Tools.Kute/bin/Release/net8.0/Nethermind.Tools.Kute')
    parser.add_argument('--kuteArguments', type=str, help='Path to kute executable.',
                        default='./tools/Nethermind.Tools.Kute/bin/Release/net8.0/Nethermind.Tools.Kute')
    parser.add_argument('--ecURL', type=str, help='Execution client where we will be running kute url.',
                        default='http://localhost:8551')

    # Parse command-line arguments
    args = parser.parse_args()

    # Get client name and test case folder from command-line arguments
    tests_paths = args.testsPath
    jwt_path = args.jwtPath
    execution_url = args.ecURL
    output_folder = args.output
    executables['dotnet'] = args.dotnetPath
    executables['kute'] = args.kutePath
    kute_arguments = args.kuteArguments
    response_file = args.responseFile

    response_path = os.path.join(output_folder, response_file)

    # Create the output folder if it doesn't exist
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # It will run Kute, might take some time
    run_command(tests_paths, jwt_path, response_path, execution_url, kute_arguments)

    # Print Computer specs
    partial_results = print_computer_specs()

    processed_output = process_output(response_path)

    # Print results after getting them.
    print_final_results(response_path, partial_results, processed_output)


if __name__ == '__main__':
    main()
