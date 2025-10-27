# Create argument parser
import argparse
import os
import subprocess
import tempfile

LOKI_ENDPOINT_ENV_VAR = "LOKI_ENDPOINT"
PROMETHEUS_ENDPOINT_ENV_VAR = "PROMETHEUS_ENDPOINT"
PROMETHEUS_USERNAME_ENV_VAR = "PROMETHEUS_USERNAME"
PROMETHEUS_PASSWORD_ENV_VAR = "PROMETHEUS_PASSWORD"

executables = {
    "kute": "./nethermind/tools/artifacts/bin/Nethermind.Tools.Kute/release/Nethermind.Tools.Kute"
}

def get_command_env(
    client: str,
    test_case_file: str,
):
    command_env = os.environ.copy()

    loki_endpoint = command_env.get(LOKI_ENDPOINT_ENV_VAR, "")
    prometheus_endpoint = command_env.get(PROMETHEUS_ENDPOINT_ENV_VAR, "")
    prometheus_username = command_env.get(PROMETHEUS_USERNAME_ENV_VAR, "")
    prometheus_password = command_env.get(PROMETHEUS_PASSWORD_ENV_VAR, "")

    test_case_name = os.path.splitext(os.path.split(test_case_file)[-1])[0]

    command_env["GA_LOKI_REMOTE_WRITE_URL"] = loki_endpoint
    command_env["GA_PROMETHEUS_REMOTE_WRITE_URL"] = prometheus_endpoint
    command_env["GA_PROMETHEUS_REMOTE_WRITE_USERNAME"] = prometheus_username
    command_env["GA_PROMETHEUS_REMOTE_WRITE_PASSWORD"] = prometheus_password
    command_env["GA_METRICS_LABELS_INSTANCE"] = f"{client}-{test_case_name}"
    command_env["GA_METRICS_LABELS_TESTNET"] = "gas-benchmarks-testnet"
    command_env["GA_METRICS_LABELS_EXECUTION_CLIENT"] = client

    return command_env


def run_command(
    client,
    test_case_file,
    jwt_secret,
    response,
    ec_url,
    kute_extra_arguments,
    skip_forkchoice=True,
):
    input_path = test_case_file
    temp_path = None
    if skip_forkchoice:
        try:
            with open(test_case_file, "r", encoding="utf-8") as original:
                lines = original.readlines()
        except OSError:
            lines = []
        filtered_lines = [line for line in lines if "engine_forkchoiceUpdated" not in line]
        if len(filtered_lines) != len(lines):
            temp_file = tempfile.NamedTemporaryFile(
                mode="w", delete=False, encoding="utf-8", suffix=".txt"
            )
            try:
                temp_file.writelines(filtered_lines)
            finally:
                temp_file.close()
            input_path = temp_file.name
            temp_path = temp_file.name
    # Add logic here to run the appropriate command for each client
    command = (
        f"{executables['kute']} -i \"{input_path}\" -s {jwt_secret} -r \"{response}\" -a {ec_url} "
        f"{kute_extra_arguments} "
    )
    # Prepare env variables
    command_env = get_command_env(
        client,
        test_case_file,
    )

    results = subprocess.run(
        command, shell=True, capture_output=True, text=True, env=command_env
    )
    if temp_path and os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except OSError:
            pass
    print(results.stderr, end="")
    return results.stdout

def save_to(output_folder, file_name, content):
    output_path = os.path.join(output_folder, file_name)
    with open(output_path, "w") as file:
        file.write(content)

def main():
    parser = argparse.ArgumentParser(description="Benchmark script")
    parser.add_argument(
        "--testsPath", type=str, help="Path to test case folder", default="small_tests"
    )
    parser.add_argument("--client", type=str, help="Name of the client we are testing")
    parser.add_argument(
        "--run", type=int, help="Number of times the test was run", default=0
    )
    parser.add_argument(
        "--jwtPath",
        type=str,
        help="Path to the JWT secret used to communicate with the client you want to test",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output folder for metrics charts generation. If the folder does "
        "not exist will be created.",
        default="results",
    )
    # Executables path
    parser.add_argument(
        "--dotnetPath",
        type=str,
        help="Path to dotnet executable, needed if testing nethermind and "
        "you need to use something different to dotnet.",
        default="dotnet",
    )
    parser.add_argument(
        "--kutePath",
        type=str,
        help="Path to kute executable.",
        default=executables["kute"],
    )
    parser.add_argument(
        "--kuteArguments", type=str, help="Extra arguments for Kute.", default=""
    )
    parser.add_argument(
        "--ecURL",
        type=str,
        help="Execution client where we will be running kute url.",
        default="http://localhost:8551",
    )
    parser.add_argument(
        "--warmupPath", type=str, help="Set path to warm up file.", default=""
    )
    parser.add_argument(
        "--skipForkchoice",
        action="store_true",
        help="Ignore engine_forkchoiceUpdated requests contained in the test input.",
    )

    # Parse command-line arguments
    args = parser.parse_args()

    # Get client name and test case folder from command-line arguments
    tests_paths = args.testsPath
    jwt_path = args.jwtPath
    execution_url = args.ecURL
    output_folder = args.output
    executables["dotnet"] = args.dotnetPath
    executables["kute"] = args.kutePath
    kute_arguments = args.kuteArguments
    warmup_file = args.warmupPath
    client = args.client
    run = args.run

    # Create the output folder if it doesn't exist
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    if warmup_file != "":
        warmup_response_file = os.path.join(
            output_folder, f"warmup_{client}_response_{run}.txt"
        )
        warmup_response = run_command(
            client,
            warmup_file,
            jwt_path,
            warmup_response_file,
            execution_url,
            kute_arguments,
            skip_forkchoice=args.skipForkchoice,
        )
        save_to(output_folder, f"warmup_{client}_results_{run}.txt", warmup_response)

    # if test case path is a folder, run all the test cases in the folder
    if os.path.isdir(tests_paths):
        tests_cases = []
        for root, _, files in os.walk(tests_paths):
            if len(files) == 0:
                continue
            for file in files:
                if file.endswith("metadata.txt"):
                    continue
                tests_cases.append(os.path.join(root, file))
        for test_case_path in tests_cases:
            name = test_case_path.split("/")[-1].split(".")[0]
            response_file = os.path.join(
                output_folder, f"{client}_response_{run}_{name}.txt"
            )
            print(
                f"Running {client} for the {run} time with test case {test_case_path}"
            )
            response = run_command(
                client,
                test_case_path,
                jwt_path,
                response_file,
                execution_url,
                kute_arguments,
                skip_forkchoice=args.skipForkchoice,
            )
            save_to(output_folder, f"{client}_results_{run}_{name}.txt", response)
        return
    else:        
        test_case_without_extension = os.path.splitext(tests_paths.split('/')[-1])[0]
        response_file = os.path.join(output_folder, f'{client}_response_{run}_{test_case_without_extension}.txt')
        print(f"Running {client} for the {run} time with test case {tests_paths}")
        response = run_command(
            client,
            tests_paths,
            jwt_path,
            response_file,
            execution_url,
            kute_arguments,
            skip_forkchoice=args.skipForkchoice,
        )
        save_to(output_folder, f'{client}_results_{run}_{test_case_without_extension}.txt',
                response)


if __name__ == '__main__':
    main()
