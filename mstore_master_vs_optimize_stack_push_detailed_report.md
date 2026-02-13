# MSTORE benchmark comparison: master vs optimize-stack-push

Method: `run-native.ps1 -TestsPath eest_tests -Filter MSTORE -Runs 5 -SkipPrepareTools` on both repos; comparison metric is p95 of `engine_newPayloadV4 last` (NP ms, lower is better).

## Overall

- Run 1: compared 60, improved 45, regressed 15, geometric speedup 1.0782x
- Run 2 (rerun): compared 60, improved 54, regressed 6, geometric speedup 1.1349x
- Stability across runs: same sign 45/60, sign changed 15/60, consistently improved 42, consistently regressed 3

## Pattern by opcode

| Group | Run1 improved/regressed | Run1 avg delta % | Run2 improved/regressed | Run2 avg delta % |
|---|---:|---:|---:|---:|
| MSTORE | 25/5 | -7.37 | 27/3 | -10.19 |
| MSTORE8 | 20/10 | 0.82 | 27/3 | -11.39 |

## Pattern by memory size

| Group | Run1 improved/regressed | Run1 avg delta % | Run2 improved/regressed | Run2 avg delta % |
|---|---:|---:|---:|---:|
| 0 | 11/1 | -0.42 | 11/1 | -12.44 |
| 1024 | 9/3 | -1.08 | 9/3 | -0.09 |
| 10240 | 5/7 | 9.4 | 10/2 | -8.62 |
| 256 | 11/1 | -14.68 | 12/0 | -15.27 |
| 32 | 9/3 | -9.6 | 12/0 | -17.52 |

## Pattern by offset_initialized

| Group | Run1 improved/regressed | Run1 avg delta % | Run2 improved/regressed | Run2 avg delta % |
|---|---:|---:|---:|---:|
| False | 24/6 | -9.22 | 26/4 | -10.1 |
| True | 21/9 | 2.67 | 28/2 | -11.48 |

## Pattern by offset

| Group | Run1 improved/regressed | Run1 avg delta % | Run2 improved/regressed | Run2 avg delta % |
|---|---:|---:|---:|---:|
| 0 | 12/8 | 7.59 | 16/4 | -7.9 |
| 1 | 18/2 | -13.87 | 19/1 | -10.35 |
| 31 | 15/5 | -3.55 | 19/1 | -14.12 |

## Consistently improved tests (both runs)

| avg delta % | run1 delta % | run2 delta % | mem_size | init | offset | opcode | test |
|---:|---:|---:|---:|---|---:|---|---|
| -42.84 | -45.36 | -40.33 | 0 | False | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_0-offset_initialized_False-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| -34.74 | -36.26 | -33.23 | 0 | False | 31 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_0-offset_initialized_False-offset_31-opcode_MSTORE8]-gas-value_100M.txt |
| -29.61 | -38.45 | -20.76 | 256 | True | 1 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_256-offset_initialized_True-offset_1-opcode_MSTORE8]-gas-value_100M.txt |
| -27.86 | -35.85 | -19.88 | 0 | True | 1 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_0-offset_initialized_True-offset_1-opcode_MSTORE8]-gas-value_100M.txt |
| -25.18 | -38.98 | -11.39 | 0 | True | 1 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_0-offset_initialized_True-offset_1-opcode_MSTORE]-gas-value_100M.txt |
| -23.34 | -27.28 | -19.39 | 256 | False | 0 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_256-offset_initialized_False-offset_0-opcode_MSTORE]-gas-value_100M.txt |
| -22.7 | -32.14 | -13.26 | 32 | False | 1 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_32-offset_initialized_False-offset_1-opcode_MSTORE8]-gas-value_100M.txt |
| -21.12 | -19.98 | -22.25 | 256 | True | 31 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_256-offset_initialized_True-offset_31-opcode_MSTORE]-gas-value_100M.txt |
| -21.01 | -27.36 | -14.66 | 32 | True | 0 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_32-offset_initialized_True-offset_0-opcode_MSTORE]-gas-value_100M.txt |
| -20.08 | -20.17 | -20 | 256 | False | 1 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_256-offset_initialized_False-offset_1-opcode_MSTORE8]-gas-value_100M.txt |
| -19.18 | -23.52 | -14.83 | 32 | False | 1 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_32-offset_initialized_False-offset_1-opcode_MSTORE]-gas-value_100M.txt |
| -18.6 | -14.93 | -22.26 | 32 | False | 31 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_32-offset_initialized_False-offset_31-opcode_MSTORE8]-gas-value_100M.txt |
| -17.76 | -15.7 | -19.81 | 256 | True | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_256-offset_initialized_True-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| -17.75 | -14.43 | -21.07 | 32 | False | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_32-offset_initialized_False-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| -16.92 | -15.65 | -18.2 | 32 | False | 31 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_32-offset_initialized_False-offset_31-opcode_MSTORE]-gas-value_100M.txt |

## Consistently regressed tests (both runs)

| avg delta % | run1 delta % | run2 delta % | mem_size | init | offset | opcode | test |
|---:|---:|---:|---:|---|---:|---|---|
| 28.35 | 42.53 | 14.17 | 10240 | True | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_True-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| 25.9 | 6.15 | 45.65 | 1024 | False | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_1024-offset_initialized_False-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| 17.46 | 28.14 | 6.78 | 10240 | True | 0 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_True-offset_0-opcode_MSTORE]-gas-value_100M.txt |

## Sign-flip cases (improved <-> regressed)

| run1 sign | run2 sign | run1 delta % | run2 delta % | mem_size | init | offset | opcode | test |
|---|---|---:|---:|---:|---|---:|---|---|
| regressed | improved | 239.87 | -7.76 | 0 | True | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_0-offset_initialized_True-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| improved | regressed | -33.72 | 27.65 | 0 | False | 1 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_0-offset_initialized_False-offset_1-opcode_MSTORE8]-gas-value_100M.txt |
| regressed | improved | 25.87 | -32.31 | 32 | True | 31 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_32-offset_initialized_True-offset_31-opcode_MSTORE]-gas-value_100M.txt |
| regressed | improved | 28.44 | -27.88 | 10240 | False | 31 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_False-offset_31-opcode_MSTORE]-gas-value_100M.txt |
| regressed | improved | 42.17 | -12.26 | 10240 | False | 31 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_False-offset_31-opcode_MSTORE8]-gas-value_100M.txt |
| regressed | improved | 25.89 | -17.05 | 10240 | False | 1 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_False-offset_1-opcode_MSTORE8]-gas-value_100M.txt |
| regressed | improved | 19.85 | -16.92 | 32 | True | 1 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_32-offset_initialized_True-offset_1-opcode_MSTORE8]-gas-value_100M.txt |
| regressed | improved | 4.19 | -27.22 | 10240 | False | 0 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_False-offset_0-opcode_MSTORE]-gas-value_100M.txt |
| improved | regressed | -8.81 | 20.08 | 1024 | False | 31 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_1024-offset_initialized_False-offset_31-opcode_MSTORE]-gas-value_100M.txt |
| improved | regressed | -1.98 | 24.92 | 1024 | False | 0 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_1024-offset_initialized_False-offset_0-opcode_MSTORE]-gas-value_100M.txt |
| regressed | improved | 14.06 | -8.72 | 1024 | True | 31 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_1024-offset_initialized_True-offset_31-opcode_MSTORE]-gas-value_100M.txt |
| regressed | improved | 2.72 | -20.01 | 32 | True | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_32-offset_initialized_True-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| regressed | improved | 7.1 | -15.02 | 1024 | True | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_1024-offset_initialized_True-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| regressed | improved | 2.5 | -17.47 | 256 | False | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_256-offset_initialized_False-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| regressed | improved | 9.52 | -3.66 | 10240 | True | 31 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_True-offset_31-opcode_MSTORE8]-gas-value_100M.txt |
