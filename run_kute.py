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
    print(command)
    results = subprocess.run(command, shell=True, capture_output=True, text=True)
    print(results.stderr)
    return results.stdout


def save_to(output_folder, file_name, content):
    output_path = os.path.join(output_folder, file_name)
    with open(output_path, "w") as file:
        file.write(content)


def main():
    parser = argparse.ArgumentParser(description='Benchmark script')
    parser.add_argument('--testsPath', type=str, help='Path to test case folder', default='small_tests')
    parser.add_argument('--client', type=str, help='Name of the client we are testing')
    parser.add_argument('--run', type=int, help='Number of times the test was run', default=0)
    parser.add_argument('--jwtPath', type=str,
                        help='Path to the JWT secret used to communicate with the client you want to test')
    parser.add_argument('--output', type=str, help='Output folder for metrics charts generation. If the folder does '
                                                   'not exist will be created.',
                        default='results')
    # Executables path
    parser.add_argument('--dotnetPath', type=str, help='Path to dotnet executable, needed if testing nethermind and '
                                                       'you need to use something different to dotnet.',
                        default='dotnet')
    parser.add_argument('--kutePath', type=str, help='Path to kute executable.',
                        default=executables["kute"])
    parser.add_argument('--kuteArguments', type=str, help='Extra arguments for Kute.',
                        default='')
    parser.add_argument('--ecURL', type=str, help='Execution client where we will be running kute url.',
                        default='http://localhost:8551')
    parser.add_argument('--warmupPath', type=str, help='Set path to warm up file.', default='')

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
    warmup_file = args.warmupPath
    client = args.client
    run = args.run

    # Create the output folder if it doesn't exist
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    if warmup_file != '':
        warmup_response_file = os.path.join(output_folder, f'warmup_{client}_response_{run}.txt')
        warmup_response = run_command(warmup_file, jwt_path, warmup_response_file, execution_url, kute_arguments)
        save_to(output_folder, f'warmup_{client}_results_{run}.txt', warmup_response)

    # Print Computer specs
    computer_specs = print_computer_specs()
    save_to(output_folder, 'computer_specs.txt', computer_specs)

    # if test case path is a folder, run all the test cases in the folder
    if os.path.isdir(tests_paths):
        for test_case in os.listdir(tests_paths):
            test_case_path = os.path.join(tests_paths, test_case)
            name = test_case.split('.')[0]
            response_file = os.path.join(output_folder, f'{client}_response_{run}_{name}.txt')
            print(f"Running {client} for the {run} time with test case {test_case}")
            response = run_command(test_case_path, jwt_path, response_file, execution_url, kute_arguments)
            test_case_without_extension = os.path.splitext(test_case)[0]
            save_to(output_folder, f'{client}_results_{run}_{test_case_without_extension}.txt',
                    response)
        return
    else:
        response_file = os.path.join(output_folder, f'{client}_response_{run}.txt')
        print(f"Running {client} for the {run} time with test case {tests_paths}")
        response = run_command(tests_paths, jwt_path, response_file, execution_url, kute_arguments)
        test_case_without_extension = os.path.splitext(tests_paths.split('/')[-1])[0]
        save_to(output_folder, f'{client}_results_{run}_{test_case_without_extension}.txt',
                response)


if __name__ == '__main__':
    main()
