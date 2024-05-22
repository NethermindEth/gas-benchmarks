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
mkdir -p results
```

## Running the Benchmarks

### Script: Run all

For running the whole pipeline, you can use the `run.sh` script.

```sh
bash run.sh -t "testPath" -w "warmupFilePath" -c "client1,client2" -r runNumber -i "image1,image2"
```

Example run:
```shell
run.sh -t "tests/" -w "warmup/warmup-1000bl-16wi-24tx.txt" -c "nethermind,geth,reth" -r 8
```

Flags:
- `--t` it's used to define the path where the tests are located.
- `--w` it's used to define the path where the warmup file is located.
- `--c` it's used to define the clients that you want to run the benchmarks. Separate the clients with a comma.
- `--r` it's used to define the number of iterations that you want to run the benchmarks. It's a numeric value.
- `--i` it's used to define the images that you want to use to run the benchmarks. Separate the images with a comma, and match the clients. Use `default` if you want to ignore the values.


Now you're ready to run the benchmarks locally!