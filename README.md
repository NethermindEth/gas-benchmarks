# Gas Benchmarks

This repository contains scripts to run benchmarks across multiple clients.
Follow the instructions below to run the benchmarks locally.

## Prerequisites

Make sure you have the following installed on your system:

- Python 3.10
- Docker
- Docker Compose
- .NET 8.0.x
- `make` (for running make commands)

## Setup

1. **Clone the repository:**

```sh
git clone https://github.com/nethermindeth/gas-benchmarks.git
cd gas-benchmarks
```

2. **Install Python dependencies:**

```sh
pip install -r requirements.txt
```

3. **Prepare Kute dependencies (specific to Nethermind):**

```sh
make prepare_tools
```

4. **Create a results directory:**

```sh
mkdir results
```

## Running the Benchmarks

### Environment Variables

Before running the benchmarks, define the following environment variables:

- `TEST_PATH`: Path to the directory containing test files (default: `tests/`).
- `WARMUP_FILE`: Path to the warm-up file (default: `warmup/warmup-1000bl-16wi-24tx.txt`). Leave empty if no warm-up is
  needed.
- `CLIENTS`: Comma-separated list of client names (e.g., `nethermind,reth,geth,erigon`). Default
  is `nethermind,geth,reth`.
- `RUNS`: Number of runs for the application (default: 8).
- `IMAGES`: Comma-separated list of images for the clients (default: `default`).

### Example

Set up the environment variables in your shell:

```sh
export TEST_PATH='tests/'
export WARMUP_FILE='warmup/warmup-1000bl-16wi-24tx.txt'
export CLIENTS='nethermind,geth,reth'
export RUNS=8
export IMAGES='default'
```

### Run Benchmarks

#### Setup the node

```
python3 setup_node.py --client $client --image $image
```

Flags:

- `--client` it's used for select the clients that you want to setup, supported now, nethermind, geth, reth, erigon.
- `--image` it's used to define the image you want to use setup your node.

#### Running the benchmarks

```
python3 run_kute.py --output results --testsPath "$test_dir" --jwtPath $jwtsecretpath --warmupPath $warmupfile --client $client --run $run
```

Flags:

- `--output` it's used to define the output directory where the results will be stored.
- `--testsPath` it's used to define the path where the tests are located.
- `--jwtPath` it's used to define the path where the jwt secret is located.
- `--warmupPath` it's used to define the path where the warmup file is located.
- `--client` it's used to define the client that you want to run the benchmarks, needs to match with the client name
  that you used in the setup node.
- `--run` it's used to define the iteration number of the running tests. If you want multiples run, you need to run this
  command with different run numbers.

### Cleanup

After running the benchmarks, you can clean up Docker containers and remove data directories:

```sh
cd "scripts/$client"
docker-compose down
sudo rm -rf execution-data
cd ../..
```

### Reporting

To generate a report from the results, run the following command:

```sh
python3 results_2.py --resultsPath $resultsPath --clients $clients --testsPath $testsPath --runs $runs
```

Flags:

- `--resultsPath` it's used to define the path where the results are located.
- `--clients` it's used to define the clients that you want to generate the report. Separate the clients with a comma.
- `--testsPath` it's used to define the path where the tests are located.
- `--runs` it's used to define the number of runs that you want to generate the report. It's linked to how many times
  you run the benchmarks.

### Script: Run all

If you want to do all the above without step by step, you can use the `run.sh` script.

```sh
bash run.sh -t "$testPath" -w "$warmupFilePath" -c "$clients" -r $run -i "$images"
```

Flags:
- `--t` it's used to define the path where the tests are located.
- `--w` it's used to define the path where the warmup file is located.
- `--c` it's used to define the clients that you want to run the benchmarks. Separate the clients with a comma.
- `--r` it's used to define the number of iterations that you want to run the benchmarks.
- `--i` it's used to define the images that you want to use to run the benchmarks. Separate the images with a comma.

## Notes

- The `setup_node.py` script is used to set up the client nodes.
- The `run_kute.py` script runs the actual benchmarks and stores the results in the `results` directory.
- The `results_2.py` script generates a report from the results.
- Modify the paths and client/image names as needed based on your setup.

Now you're ready to run the benchmarks locally!
If you encounter any issues, refer to the scripts and adjust the commands as necessary.
