# engine_newPayload comparison (p95 ms)

| Test | Baseline p95 (ms) | Optimized p95 (ms) | Delta (ms) | Delta (%) |
|---|---:|---:|---:|---:|
| benchmark_compute_precompile_test_ecrecover.py__test_ecrecover[fo... | 1886.91 | 1561.82 | -325.09 | -17.23% |
| benchmark_compute_precompile_test_identity.py__test_identity_fixe... | 433.43 | 171.37 | -262.06 | -60.46% |
| benchmark_compute_precompile_test_identity.py__test_identity_fixe... | 385.46 | 153.45 | -232.01 | -60.19% |
| benchmark_compute_precompile_test_identity.py__test_identity_fixe... | 264.44 | 136.2 | -128.24 | -48.50% |
| benchmark_compute_precompile_test_identity.py__test_identity_fixe... | 341.21 | 225.72 | -115.49 | -33.85% |
| benchmark_compute_precompile_test_identity.py__test_identity[fork... | 426.5 | 311.32 | -115.18 | -27.01% |
| benchmark_compute_instruction_test_call_context.py__test_returnda... | 409.89 | 385.54 | -24.35 | -5.94% |
| benchmark_compute_instruction_test_call_context.py__test_returnda... | 169.63 | 156.25 | -13.38 | -7.89% |
| benchmark_compute_instruction_test_call_context.py__test_returnda... | 156.44 | 163.53 | 7.09 | 4.53% |
| benchmark_compute_instruction_test_call_context.py__test_returnda... | 166.93 | 595.96 | 429.03 | 257.01% |
