# Gas Benchmarks — Contributors Guide

## What this project is (and why it exists)

Gas Benchmarks measures and compares performance of Ethereum execution clients using deterministic engine payloads. It runs curated or captured tests across clients (Nethermind, Geth, Reth, Besu, Erigon, etc.), collects raw results, aggregates metrics (MGas/s ), renders reports, and can ingest data into PostgreSQL for Grafana dashboards.

Reference: see [README.md](README.md) for full setup and usage details.

## Quick start

1) Prereqs: Python 3.10, Docker, Docker Compose, .NET 8, make
2) Install deps and prepare tools:

```sh
pip install -r requirements.txt
make prepare_tools
mkdir -p results
```

3) Run benchmarks (example):

```sh
bash run.sh -t "tests/" -w "warmup-tests" -c "nethermind,geth,reth" -r 3
```

Outputs land in `results/` and reports in `results/reports/`.

## Key services you’ll use

- `run.sh`: End-to-end pipeline controller. Flags: `--t`, `--w`, `--c`, `--r`, `--i` (see [README.md](README.md)).
- `setup_node.py`: Bring up a specific client stack (writes `.env`, selects genesis, runs `scripts/<client>/run.sh`).
- `run_kute.py`: Execute a single test payload against `:8551` engine endpoint with labeled environment.
- Reporting: `report_html.py`, `report_txt.py`, `report_tables.py` consume normalized metrics to produce HTML/TXT/table outputs.
- DB ETL: `generate_postgres_schema.py` (create/update table), `fill_postgres_db.py` (bulk insert runs/specs).
- Test capture: `capture_eest_tests.py` to convert EEST fixtures into newline-delimited `.txt` RPC payloads.

## Writing tests (minimal, practical)

- Place payload files under `tests/<Category>/` with filenames like `<TestCase>_<Gas>M.txt` (e.g., `MStore_150M.txt`).
- Add human-facing info to `tests/metadata.json` with `Name`, `Title`, `Description` for each test case.
- The discovery pattern is implemented by `utils.get_test_cases(tests_path)` (parses names and gas values).
- Optionally capture upstream benchmarks with:

```sh
python capture_eest_tests.py -o eest_tests -x "pattern_to_exclude"
```

Then run with `-t "eest_tests/"`.

## Running and validating

- Local runs via `run.sh`. Override images with `--i '{"client":"repo:tag"}'` or set them in `images.yaml`.
- Engine auth uses `engine-jwt/jwt.hex` (mounted or copied by compose).
- Raw artifacts:
  - Responses: `results/{client}_response_{run}_{test}_{gas}M.txt` (line-delimited JSON with `VALID` status)
  - Results: `results/{client}_results_{run}_{test}_{gas}M.txt` (measurement sections: `engine_newPayloadV4` → fields incl. `max`)
- If a response line isn’t `VALID`, it won’t be aggregated.

## Checking reports

- HTML: open `results/reports/index.html` (sortable tables; includes computer specs if present).
- TXT/table summaries: see `results/reports/` and `reports/tables_norm.txt`.
- The reporters use `tests/metadata.json` to print titles/descriptions and compute Min/Max/p50/p95/p99 and N.

## Contributing changes

- Keep logic pure where possible: parsing/aggregation in `utils.py`; orchestration in `run.sh`.
- Follow the adapter layout for new clients under `scripts/<client>/`:
  - `docker-compose.yaml`, `run.sh`, `jwtsecret`; add genesis under `scripts/genesisfiles/<client>/`
  - Set/override default images in `images.yaml` (or via `--i`)
- Add a new report format by consuming `utils.get_gas_table(...)` and `tests/metadata.json`.
- Extend DB schema via `generate_postgres_schema.py` and map fields in `fill_postgres_db.py`.
- Prefer explicit artifacts (files) over hidden state; it simplifies debugging and comparisons.

## CI / continuous metrics

- Use `run_and_post_metrics.sh` to loop: pull → run → ingest → cleanup.
- DB setup: run `generate_postgres_schema.py` once; then point `fill_postgres_db.py` to `results/`.
- Only Kute tests supported for now.

## Troubleshooting (quick checks)

- Engine not reachable: ensure `scripts/<client>/docker-compose.yaml` stack is up and `:8551` is exposed.
- Invalid responses: confirm JWT (`engine-jwt/jwt.hex`) and genesis file selection.
- Missing Kute: run `make prepare_tools` and check `run_kute.py` paths.
- No results: verify test filenames and gas suffixes, and that `run.sh` flags point to the correct paths.

## Using Kute — Internal Benchmarking Tool

### What is Kute?

Kute is a .NET CLI tool used here to replay JSON‑RPC engine messages against an execution client and measure performance. It simulates the Consensus Layer sending `engine_*` calls (plus optional `eth_*`) to the client at `:8551`, validates responses, and aggregates timings.

Location: `nethermind/tools/Nethermind.Tools.Kute/` (after running `make prepare_tools`) or `https://github.com/NethermindEth/nethermind/tree/master/tools/Kute`

Why it matters in this repo: `run_kute.py` wraps Kute to execute per‑test payload files; reporters parse Kute outputs to compute MGas/s metrics and build reports.

### Build

Requires .NET SDK (8+). From the tool directory:

```sh
cd nethermind/tools/Nethermind.Tools.Kute
dotnet build -c Release
```

The `run_kute.py` wrapper points to the built binary path under `nethermind/tools/artifacts/bin/Nethermind.Tools.Kute/release/Nethermind.Tools.Kute` (prepared by `make prepare_tools`).

### Core workflow (how it works)

- Reads messages from a file or directory (`-i/--input`). Each line is a JSON‑RPC request or a JSON batch.
- Authenticates using JWT (`-s/--secret`) with optional TTL (`-t/--ttl`).
- Submits to `:8551` (`-a/--address`) sequentially or at a target RPS (`--rps`).
- Optionally unwraps batch requests into single requests (`-u/--unwrapBatch`).
- Validates responses (non‑error + newPayload checks) unless `--dry` is used.
- Emits metrics (per‑method durations, batch durations, totals) and can trace responses to a file (`-r/--responses`).

### CLI options (most used)

- `-i, --input <path>`: File or directory of messages (required)
- `-s, --secret <path>`: Hex JWT secret file (required)
- `-a, --address <URL>`: Engine address (default `http://localhost:8551`)
- `-t, --ttl <seconds>`: JWT TTL seconds (default 60)
- `-o, --output <Report|Json>`: Metrics output format (default Report)
- `-r, --responses <path>`: Write JSON‑RPC responses to file
- `-f, --filters <patterns>`: Comma‑separated regex; supports limits: `pattern=NN`
- `-e, --rps <int>`: Requests per second (>0 throttles; <=0 sequential)
- `-u, --unwrapBatch`: Treat batch items as individual requests
- `-d, --dry`: Don’t send requests (still builds auth token)
- `-p, --progress`: Show progress (startup overhead)

### Using Kute via this repo

- Wrapper: `run_kute.py` builds the command and sets labels for remote metrics (Loki/Prometheus) using env vars. It writes stdout to `results/{client}_results_*` and engine responses to `results/{client}_response_*`.
- Orchestrator: `run.sh` calls the wrapper per test case, per client, per run; reporters then compute aggregates from these artifacts.

Minimal direct usage from repo root (example):

```sh
./nethermind/tools/artifacts/bin/Nethermind.Tools.Kute/release/Nethermind.Tools.Kute \
  -i tests/MStore/MStore_150M.txt \
  -s engine-jwt/jwt.hex \
  -a http://localhost:8551 \
  -r results/nethermind_response_1_MStore_150M.txt \
  -o Report
```

### Writing tests (message files)

A “test” for Kute is a newline‑delimited file of JSON‑RPC requests (or batches). In this repo:

- Test files live under `tests/` (grouped by category) and end with `_<Gas>M.txt` (e.g., `MStore_150M.txt`).
- `utils.get_test_cases(tests_path)` discovers test names and gas variants by filename pattern; reporters map test names to titles/descriptions via `tests/metadata.json`.
- Lines must be valid JSON objects or JSON arrays (for batch). If using `--unwrapBatch`, each array item will be sent individually.
- To record real traffic, run a client with RpcRecorderState and export logs, or use `capture_eest_tests.py` to transform EEST fixtures into `.txt` lines.

### Interpreting outputs

- Metrics stdout (Report|Json): per‑method durations, totals, counts; gas-benchmarks parsers convert these to MGas/s and percentiles.
- Response trace file (`-r`): line‑delimited JSON responses, used to validate `VALID` status for aggregation.

## EEST — Execution Spec Tests (Benchmark Guide)

### What is EEST?

Execution Spec Tests (EEST) is the canonical test suite and tooling for Ethereum execution clients. It can generate and run test cases across forks and exposes multiple execution modes:

- consume direct: call a client’s test interface for fast EVM dev loops
- consume rlp: feed RLP blocks to simulate historical sync
- consume engine: drive the Engine API (post-merge) with payloads
- execute remote/hive: run Python tests against a live client via RPC, or on a local Hive network

In this repo, EEST is used to source benchmark scenarios and produce deterministic payloads for performance measurements.

### Why use EEST here?

- Authoritative scenarios for protocol/fork coverage
- Deterministic inputs across clients
- Multiple execution backends (Engine API, RLP, direct) to stress different code paths
- Works both locally and in CI/Hive setups

### Setup (vendored or standalone)

Option A (after running `make prepare_tools`) — use the vendored tree: `execution-spec-tests/` is included in this repo.

Option B — clone upstream (requires uv):

```sh
git clone https://github.com/ethereum/execution-spec-tests
cd execution-spec-tests
uv python install 3.11
uv python pin 3.11
uv sync --all-extras
```

### Running benchmark tests (quick recipes)

- Execute on a live client (remote):

```sh
uv run execute remote \
  --fork=Prague \
  --rpc-endpoint=http://127.0.0.1:8545 \
  --rpc-chain-id=1 \
  --rpc-seed-key 0x<private_key> \
  tests -- -m benchmark -n 1
```

- Execute a specific test file/case remotely:

```sh
uv run execute remote --fork=Prague --rpc-endpoint=http://127.0.0.1:8545 --rpc-chain-id=1 --rpc-seed-key 0x<key> \
  ./tests/prague/.../test_x.py::test_case
```

- Run Engine API simulator with JSON fixtures (parallel):

```sh
uv run consume engine --input=<fixture_dir> -n auto
```

- List collected tests without running:

```sh
uv run consume engine --input=<fixture_dir> --collect-only -q
```

- RLP mode (pre/post-merge forks; sync path):

```sh
uv run consume rlp --input=<fixture_dir>
```

Notes:

- `--fork` must match the target fork (e.g., Prague/Osaka. Previous forks are not supported yet).
- Remote mode needs a funded key (`--rpc-seed-key`) and `--rpc-chain-id`.
- Use pytest filters `-k` or marks `-m benchmark` to select benchmark tests.

### Writing benchmark tests (from EEST docs)

- place tests under `tests/benchmark/` (fork subtrees as needed) and make them filterable with `-m benchmark`.
- avoid randomness and time-based values; explicitly set addresses, nonces, balances, gas, and data sizes so payloads can be reproduced.
- use `@pytest.mark.valid_from("<Fork>")` / `@pytest.mark.valid_until("<Fork>")` at function/class/module level to scope forks.
- prefer `@pytest.mark.parametrize(..., ids=[...])` to encode size/shape variations (e.g., payload byte sizes, number of txs) for consistent benchmark IDs.
- one bottleneck per test (e.g., SSTORE cold/warm, KECCAK sizes, precompile inputs). Keep pre-state minimal and re-use shared pre-state when possible to reduce setup noise.
- choose the backend that exercises the intended path (Engine for post-merge consensus path, RLP for sync/import, direct for fast EVM-only loops). Benchmarks should state their intent.
- ensure tests can run under `execute remote` and/or produce fixtures consumable by `consume engine/rlp`.

Reference: EEST Benchmark tests guide: `https://github.com/ethereum/execution-spec-tests/blob/main/docs/writing_tests/benchmarks.md`

### Integrating EEST with gas-benchmarks

- Capture/convert fixtures to payload files for Kute with the repo tool:

```sh
python capture_eest_tests.py -o eest_tests -x "pattern_to_exclude"
```

This reads EEST fixtures and writes newline-delimited JSON-RPC payload `.txt` files (using `utils/make_rpc.jq`).

- Run the gas-benchmarks pipeline with `-t eest_tests/` to benchmark across clients:

```sh
bash run.sh -t "eest_tests/" -w "warmup-tests" -c "client1,client2" -r 3
```

### Useful options (pytest/uv)

- Parallelism: `-n auto` (requires pytest-xdist) for `uv run consume engine ... -n auto`
- Durations: `--durations=10` to print slowest tests
- Verbosity/debug: `-v`, `-x`, `--pdb`, `-s`

### CI and benchmark suites

- Upstream CI target for deployed-fork benchmarks: `uvx --with=tox-uv tox -e tests-deployed-benchmark`
- Local Hive (for execute): run Hive dev mode and point EEST to the simulator (`HIVE_SIMULATOR`)

### EEST vs Kute (how they differ; when to use which)

- Purpose
  - EEST: Full framework to generate and run protocol tests; can drive clients via Engine API, direct EVM harnesses, RLP sync, or live RPC.
  - Kute: Lightweight replayer that measures Engine API performance by sending prebuilt JSON-RPC messages.
- Inputs
  - EEST: Python test modules/fixtures; generates payloads/transactions, validates outcomes.
  - Kute: Newline-delimited JSON-RPC request lines or batches.
- Execution context
  - EEST: Can build state, deploy contracts, produce payloads; validates correctness against spec expectations.
  - Kute: Assumes inputs are already valid; focuses on timing/throughput and response tracing.
- When to use
  - Use EEST to author/capture benchmark scenarios and validate behavior.
  - Use Kute to replay those scenarios uniformly across clients/images and compute perf metrics.

### Minimal contributor checklist

- Install EEST dependencies (vendored or upstream) and confirm you can list/execute benchmark-marked tests.
- Create/edit benchmark tests under `tests/benchmark/` with deterministic behavior.
- Capture or transform tests into payload `.txt` files via `capture_eest_tests.py` when you want to benchmark with Kute.
- Run gas-benchmarks (`run.sh`) over clients/images; review `results/reports/`.
- For long-term tracking, ingest reports into PostgreSQL with the provided DB scripts. 

## Review before opening a PR

- Run a small benchmark subset locally and attach `results/reports/index.html` (or TXT tables) to the PR.
- Keep changes minimal and focused (new tests, new client adapter, new reporter, or utility changes).

### Thanks for contributing

Your changes help maintain reliable, comparable performance signals across Ethereum execution clients. Keep runs reproducible, artifacts explicit, and reports easy to consume.