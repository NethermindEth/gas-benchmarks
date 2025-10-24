from __future__ import annotations

import math
from typing import Dict, Iterable, Mapping, Sequence


def calculate_percentiles(
    values: Sequence[float], percentiles: Iterable[int]
) -> Dict[int, float]:
    """
    Calculate the specified percentiles using linear interpolation.
    """
    sorted_values = sorted(values)
    n = len(sorted_values)
    if n == 0:
        return {p: 0.0 for p in percentiles}
    if n == 1:
        single = sorted_values[0]
        return {p: single for p in percentiles}

    results: Dict[int, float] = {}
    for p in percentiles:
        # Clamp percentile bounds
        pct = max(0, min(100, p))
        index = (pct / 100) * (n - 1)
        lower = int(math.floor(index))
        upper = int(math.ceil(index))
        if lower == upper:
            results[p] = sorted_values[lower]
            continue
        fraction = index - lower
        lower_val = sorted_values[lower]
        upper_val = sorted_values[upper]
        results[p] = lower_val + fraction * (upper_val - lower_val)
    return results


def get_gas_table(
    client_results: Mapping[str, Mapping],
    client: str,
    test_cases: Mapping[str, Sequence[int]],
    gas_set: Iterable[int],
    method: str,
    metadata: Mapping[str, Mapping[str, str]],
) -> Dict[str, list[str]]:
    """
    Build the normalized gas table used by the HTML and TXT reports.
    """
    gas_values_per_test: Dict[str, list[float]] = {name: [] for name in test_cases}

    client_data = client_results.get(client, {})
    for test_case, _ in test_cases.items():
        case_data = client_data.get(test_case, {})
        for gas in gas_set:
            gas_runs = case_data.get(gas, {})
            run_values = gas_runs.get(method, []) if isinstance(gas_runs, dict) else []
            for run_value in run_values:
                if not run_value:
                    continue
                try:
                    normalized = (int(gas) / float(run_value)) * 1000
                except (ValueError, ZeroDivisionError):
                    continue
                gas_values_per_test[test_case].append(normalized)

    gas_table: Dict[str, list[str]] = {}
    for test_case in test_cases:
        normalized_values = gas_values_per_test[test_case]
        case_data = client_data.get(test_case, {})
        timestamp = case_data.get("timestamp", 0) if isinstance(case_data, dict) else 0

        entry = [""] * 9
        entry[8] = str(timestamp)

        if test_case in metadata:
            entry[0] = metadata[test_case].get("Title", test_case)
            entry[7] = metadata[test_case].get("Description", "")
        else:
            entry[0] = test_case
            entry[7] = "Description not found on metadata file"

        if not normalized_values:
            entry[1] = entry[2] = entry[3] = entry[4] = entry[5] = "0"
            entry[6] = "0"
            gas_table[test_case] = entry
            continue

        entry[1] = f"{min(normalized_values):.2f}"
        entry[2] = f"{max(normalized_values):.2f}"
        percentiles = calculate_percentiles(normalized_values, (50, 5, 1))
        entry[3] = f"{percentiles.get(50, 0.0):.2f}"
        entry[4] = f"{percentiles.get(5, 0.0):.2f}"
        entry[5] = f"{percentiles.get(1, 0.0):.2f}"
        entry[6] = str(len(normalized_values))

        gas_table[test_case] = entry

    return gas_table

