# MSTORE min-MGas/s comparison: absolute metrics only

Metric: for each test and branch, compute MGas/s for each of 5 runs from NP ms, then take **minimum MGas/s** (worst-case throughput).

## Overall

- Run1: compared 60, improved 42, regressed 18, avg master min MGas/s 495.28, avg branch min MGas/s 534.49, avg delta 39.2 MGas/s
- Run2: compared 60, improved 51, regressed 9, avg master min MGas/s 526.96, avg branch min MGas/s 595.39, avg delta 68.43 MGas/s
- Stability: same sign 43/60, sign flips 17/60

## Pattern by op

| Group | Run1 improved/regressed | Run1 avg master | Run1 avg branch | Run1 avg delta | Run2 improved/regressed | Run2 avg master | Run2 avg branch | Run2 avg delta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| MSTORE | 24/6 | 499.1 | 546.12 | 47.02 | 25/5 | 522.12 | 586.56 | 64.44 |
| MSTORE8 | 18/12 | 491.47 | 522.85 | 31.39 | 26/4 | 531.8 | 604.22 | 72.42 |

## Pattern by mem

| Group | Run1 improved/regressed | Run1 avg master | Run1 avg branch | Run1 avg delta | Run2 improved/regressed | Run2 avg master | Run2 avg branch | Run2 avg delta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 11/1 | 374.08 | 449.47 | 75.39 | 10/2 | 475.56 | 544.04 | 68.48 |
| 1024 | 6/6 | 559.52 | 558.66 | -0.86 | 8/4 | 556.75 | 562.77 | 6.02 |
| 10240 | 5/7 | 517.04 | 479.54 | -37.5 | 10/2 | 532.85 | 581.87 | 49.02 |
| 256 | 11/1 | 508.54 | 602.27 | 93.72 | 12/0 | 531.02 | 629.78 | 98.76 |
| 32 | 9/3 | 517.23 | 582.5 | 65.27 | 11/1 | 538.62 | 658.5 | 119.88 |

## Pattern by init

| Group | Run1 improved/regressed | Run1 avg master | Run1 avg branch | Run1 avg delta | Run2 improved/regressed | Run2 avg master | Run2 avg branch | Run2 avg delta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| False | 23/7 | 484.6 | 535.38 | 50.78 | 25/5 | 509.79 | 575.26 | 65.47 |
| True | 19/11 | 505.97 | 533.59 | 27.62 | 26/4 | 544.14 | 615.53 | 71.39 |

## Pattern by off

| Group | Run1 improved/regressed | Run1 avg master | Run1 avg branch | Run1 avg delta | Run2 improved/regressed | Run2 avg master | Run2 avg branch | Run2 avg delta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 11/9 | 490.96 | 495.84 | 4.88 | 15/5 | 529.54 | 575.26 | 45.71 |
| 1 | 17/3 | 470.35 | 557.62 | 87.27 | 18/2 | 528.13 | 595.84 | 67.71 |
| 31 | 14/6 | 524.54 | 550 | 25.46 | 18/2 | 523.21 | 615.08 | 91.87 |

## Consistently regressed (both runs)

| avg delta MGas/s | run1 master | run1 branch | run1 delta | run2 master | run2 branch | run2 delta | mem | init | off | op | test |
|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---|
| -141.56 | 554.86 | 371.89 | -182.97 | 582.29 | 482.15 | -100.14 | 10240 | True | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_True-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| -128.8 | 543.52 | 501.41 | -42.11 | 605.08 | 389.58 | -215.5 | 1024 | False | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_1024-offset_initialized_False-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| -95.83 | 558.52 | 412.51 | -146.01 | 588.2 | 542.56 | -45.64 | 10240 | True | 0 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_True-offset_0-opcode_MSTORE]-gas-value_100M.txt |
| -69.19 | 530.78 | 527.74 | -3.04 | 604.9 | 469.55 | -135.35 | 1024 | False | 0 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_1024-offset_initialized_False-offset_0-opcode_MSTORE]-gas-value_100M.txt |
| -11.29 | 569.31 | 561.31 | -8 | 597.3 | 582.72 | -14.58 | 1024 | True | 1 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_1024-offset_initialized_True-offset_1-opcode_MSTORE8]-gas-value_100M.txt |

## Largest sign-flip cases

| run1 delta MGas/s | run2 delta MGas/s | run1 master | run1 branch | run2 master | run2 branch | mem | init | off | op | test |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---|---|
| -143.86 | 224.76 | 552.99 | 409.13 | 405.97 | 630.73 | 32 | True | 31 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_32-offset_initialized_True-offset_31-opcode_MSTORE]-gas-value_100M.txt |
| -126.4 | 188.97 | 478.5 | 352.1 | 421.87 | 610.84 | 10240 | False | 31 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_False-offset_31-opcode_MSTORE]-gas-value_100M.txt |
| 191.49 | -117.74 | 316.89 | 508.38 | 500.54 | 382.8 | 0 | False | 1 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_0-offset_initialized_False-offset_1-opcode_MSTORE8]-gas-value_100M.txt |
| -259.98 | 44.93 | 350.55 | 90.57 | 562.1 | 607.03 | 0 | True | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_0-offset_initialized_True-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| -208.34 | 78.24 | 605.28 | 396.94 | 597.23 | 675.47 | 10240 | False | 31 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_False-offset_31-opcode_MSTORE8]-gas-value_100M.txt |
| -120.55 | 121.67 | 599.12 | 478.57 | 575.77 | 697.44 | 32 | True | 1 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_32-offset_initialized_True-offset_1-opcode_MSTORE8]-gas-value_100M.txt |
| -132.26 | 102.27 | 550.34 | 418.08 | 525.64 | 627.91 | 10240 | False | 1 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_False-offset_1-opcode_MSTORE8]-gas-value_100M.txt |
| -35.55 | 184.57 | 520.34 | 484.79 | 427.92 | 612.49 | 10240 | False | 0 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_10240-offset_initialized_False-offset_0-opcode_MSTORE]-gas-value_100M.txt |
| 54.64 | -111.11 | 580.9 | 635.54 | 519.89 | 408.78 | 1024 | False | 31 | MSTORE | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_1024-offset_initialized_False-offset_31-opcode_MSTORE]-gas-value_100M.txt |
| -18.85 | 143.46 | 592.15 | 573.3 | 528.08 | 671.54 | 32 | True | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_32-offset_initialized_True-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| -56.15 | 99.67 | 551.72 | 495.57 | 573.01 | 672.68 | 1024 | True | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_1024-offset_initialized_True-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
| -30.18 | 118.38 | 600.3 | 570.12 | 501.56 | 619.94 | 256 | False | 0 | MSTORE8 | tests_benchmark_compute_instruction_test_memory.py__test_memory_access[fork_Prague-benchmark-blockchain_test_engine_x-mem_size_256-offset_initialized_False-offset_0-opcode_MSTORE8]-gas-value_100M.txt |
