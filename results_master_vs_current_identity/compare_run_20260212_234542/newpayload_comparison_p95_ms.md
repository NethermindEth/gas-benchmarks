# engine_newPayload comparison (p95 ms)

| Test | Baseline p95 (ms) | Optimized p95 (ms) | Delta (ms) | Delta (%) |
|---|---:|---:|---:|---:|
| benchmark_compute_precompile_test_ecrecover.py__test_ecrecover[fo... | 1886.91 | 1485.1 | -401.81 | -21.29% |
| benchmark_compute_precompile_test_identity.py__test_identity_fixe... | 433.43 | 163.48 | -269.95 | -62.28% |
| benchmark_compute_precompile_test_identity.py__test_identity_fixe... | 385.46 | 143.97 | -241.49 | -62.65% |
| benchmark_compute_precompile_test_identity.py__test_identity_fixe... | 341.21 | 179.62 | -161.59 | -47.36% |
| benchmark_compute_precompile_test_identity.py__test_identity_fixe... | 264.44 | 127.49 | -136.95 | -51.79% |
| benchmark_compute_precompile_test_identity.py__test_identity[fork... | 426.5 | 322.18 | -104.32 | -24.46% |
| benchmark_compute_instruction_test_call_context.py__test_returnda... | 169.63 | 134.33 | -35.3 | -20.81% |
| benchmark_compute_instruction_test_call_context.py__test_returnda... | 409.89 | 386.19 | -23.7 | -5.78% |
| benchmark_compute_instruction_test_call_context.py__test_returnda... | 156.44 | 136.69 | -19.75 | -12.62% |
| benchmark_compute_instruction_test_call_context.py__test_returnda... | 166.93 | 178.56 | 11.63 | 6.97% |
