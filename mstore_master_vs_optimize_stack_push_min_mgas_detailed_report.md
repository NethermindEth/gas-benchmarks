# MSTORE min-MGas/s comparison: master vs optimize-stack-push

Metric: for each test and branch, compute MGas/s per run (5 runs) from NP ms, then take **minimum MGas/s** (worst case). Compare branch vs master on that min value (higher is better).

- Run1: improved 42, regressed 18, compared 60
- Run2: improved 51, regressed 9, compared 60
- Stability: same sign 43/60, sign flips 17/60

## Pattern by op

| Group | Run1 improved/regressed | Run1 avg delta % | Run2 improved/regressed | Run2 avg delta % |
|---|---:|---:|---:|---:|
| MSTORE | 24/6 | 11.35 | 25/5 | 13.9 |
| MSTORE8 | 18/12 | 10.99 | 26/4 | 15.29 |

## Pattern by mem

| Group | Run1 improved/regressed | Run1 avg delta % | Run2 improved/regressed | Run2 avg delta % |
|---|---:|---:|---:|---:|
| 0 | 11/1 | 25.94 | 10/2 | 18.08 |
| 1024 | 6/6 | -0.12 | 8/4 | 1.7 |
| 10240 | 5/7 | -5.6 | 10/2 | 10.8 |
| 256 | 11/1 | 20.57 | 12/0 | 18.95 |
| 32 | 9/3 | 15.05 | 11/1 | 23.44 |

## Pattern by init

| Group | Run1 improved/regressed | Run1 avg delta % | Run2 improved/regressed | Run2 avg delta % |
|---|---:|---:|---:|---:|
| False | 23/7 | 14.63 | 25/5 | 15.28 |
| True | 19/11 | 7.71 | 26/4 | 13.91 |

## Pattern by off

| Group | Run1 improved/regressed | Run1 avg delta % | Run2 improved/regressed | Run2 avg delta % |
|---|---:|---:|---:|---:|
| 0 | 11/9 | 3.51 | 15/5 | 11.56 |
| 1 | 17/3 | 23.31 | 18/2 | 12.9 |
| 31 | 14/6 | 6.68 | 18/2 | 19.32 |

## Consistently regressed (both runs)

| avg delta % | run1 % | run2 % | mem | init | off | op | test |
|---:|---:|---:|---:|---|---:|---|---|
| -1.92 | -1.41 | -2.44 | 1024 | True | 1 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_1024-offset_initialized_True-offset_1-opcode_MSTORE8]-gas-value_100M.txt |
| -11.48 | -0.57 | -22.38 | 1024 | False | 0 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_1024-offset_initialized_False-offset_0-opcode_MSTORE]-gas-value_100M.txt |
| -16.95 | -26.14 | -7.76 | 10240 | True | 0 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_True-offset_0-opcode_MSTORE]-gas-value_100M.txt |
| -21.68 | -7.75 | -35.62 | 1024 | False | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_1024-offset_initialized_False-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| -25.09 | -32.98 | -17.2 | 10240 | True | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_True-offset_0-opcode_MSTORE8]-gas-value_100M.txt |

## Largest sign-flip cases

| run1 | run2 | run1 % | run2 % | mem | init | off | op | test |
|---|---|---:|---:|---:|---|---:|---|---|
| improved | regressed | 60.43 | -23.52 | 0 | False | 1 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_0-offset_initialized_False-offset_1-opcode_MSTORE8]-gas-value_100M.txt |
| regressed | improved | -74.16 | 7.99 | 0 | True | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_0-offset_initialized_True-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| regressed | improved | -26.01 | 55.36 | 32 | True | 31 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_32-offset_initialized_True-offset_31-opcode_MSTORE]-gas-value_100M.txt |
| regressed | improved | -26.42 | 44.79 | 10240 | False | 31 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_False-offset_31-opcode_MSTORE]-gas-value_100M.txt |
| regressed | improved | -6.83 | 43.13 | 10240 | False | 0 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_False-offset_0-opcode_MSTORE]-gas-value_100M.txt |
| regressed | improved | -34.42 | 13.1 | 10240 | False | 31 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_False-offset_31-opcode_MSTORE8]-gas-value_100M.txt |
| regressed | improved | -24.03 | 19.46 | 10240 | False | 1 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_False-offset_1-opcode_MSTORE8]-gas-value_100M.txt |
| regressed | improved | -20.12 | 21.13 | 32 | True | 1 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_32-offset_initialized_True-offset_1-opcode_MSTORE8]-gas-value_100M.txt |
| improved | regressed | 9.41 | -21.37 | 1024 | False | 31 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_1024-offset_initialized_False-offset_31-opcode_MSTORE]-gas-value_100M.txt |
| regressed | improved | -3.18 | 27.17 | 32 | True | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_32-offset_initialized_True-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| regressed | improved | -5.03 | 23.6 | 256 | False | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_256-offset_initialized_False-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| regressed | improved | -10.18 | 17.39 | 1024 | True | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_1024-offset_initialized_True-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
