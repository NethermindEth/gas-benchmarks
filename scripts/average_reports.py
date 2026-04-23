#!/usr/bin/env python3
"""
Average benchmark reports across multiple runs.

This script reads output_{client}.csv files from multiple run directories
and calculates the average for all numeric metrics, producing a single
averaged report.

Usage:
    python average_reports.py \
        --input-pattern "reports_a_run*" \
        --output reports_a \
        --clients nethermind
"""

import argparse
import csv
import glob
import os
from typing import Dict, List, Optional


# Columns that contain numeric values to be averaged
NUMERIC_COLUMNS = [
    'Max (MGas/s)',
    'p50 (MGas/s)',
    'p95 (MGas/s)',
    'p99 (MGas/s)',
    'Min (MGas/s)',
    'Duration (ms)',
    'FCU time (ms)',
    'NP time (ms)',
]

# Columns to keep as-is (take from first run)
PASSTHROUGH_COLUMNS = [
    'Title',
    'N',
    'Description',
    'Start Time',
    'End Time',
]


def load_csv(csv_path: str) -> Dict[str, Dict[str, str]]:
    """Load CSV file and return dict keyed by Title."""
    results = {}
    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = row.get('Title')
            if title:
                results[title] = row
    return results


def safe_float(val: str) -> Optional[float]:
    """Safely convert string to float."""
    if val is None or val == '' or val == 'N/A':
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def average_values(values: List[Optional[float]]) -> Optional[float]:
    """Calculate average of non-None values."""
    valid_values = [v for v in values if v is not None]
    if not valid_values:
        return None
    return sum(valid_values) / len(valid_values)


def average_reports(input_dirs: List[str], output_dir: str, clients: List[str]):
    """Average reports from multiple run directories."""
    os.makedirs(output_dir, exist_ok=True)

    for client in clients:
        client = client.strip()
        all_data: List[Dict[str, Dict[str, str]]] = []

        # Load data from each run directory
        for run_dir in input_dirs:
            csv_path = os.path.join(run_dir, f'output_{client}.csv')
            if os.path.exists(csv_path):
                data = load_csv(csv_path)
                all_data.append(data)
                print(f"Loaded {csv_path} with {len(data)} test cases")
            else:
                print(f"Warning: {csv_path} not found, skipping")

        if not all_data:
            print(f"No data found for client {client}")
            continue

        # Get all test cases (union of all runs)
        all_titles = set()
        for data in all_data:
            all_titles.update(data.keys())

        # Calculate averages
        averaged_data: Dict[str, Dict[str, str]] = {}

        for title in sorted(all_titles):
            averaged_data[title] = {}

            # For numeric columns, calculate average
            for col in NUMERIC_COLUMNS:
                values = []
                for data in all_data:
                    if title in data:
                        values.append(safe_float(data[title].get(col, '')))
                    else:
                        values.append(None)

                avg = average_values(values)
                if avg is not None:
                    averaged_data[title][col] = f'{avg:.2f}'
                else:
                    averaged_data[title][col] = ''

            # For passthrough columns, take from first available run
            for col in PASSTHROUGH_COLUMNS:
                for data in all_data:
                    if title in data and data[title].get(col):
                        averaged_data[title][col] = data[title][col]
                        break
                else:
                    averaged_data[title][col] = ''

            # Update N to reflect number of runs averaged
            averaged_data[title]['N'] = str(len(all_data))

        # Write averaged CSV
        output_path = os.path.join(output_dir, f'output_{client}.csv')
        fieldnames = ['Title', 'Max (MGas/s)', 'p50 (MGas/s)', 'p95 (MGas/s)',
                      'p99 (MGas/s)', 'Min (MGas/s)', 'N', 'Description',
                      'Start Time', 'End Time', 'Duration (ms)',
                      'FCU time (ms)', 'NP time (ms)']

        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for title in sorted(averaged_data.keys()):
                row = averaged_data[title]
                row['Title'] = title
                writer.writerow(row)

        print(f"Written averaged report to {output_path} ({len(averaged_data)} test cases)")

        # Also copy other files from the first run (index.html, tables_norm.txt, raw_results)
        # These won't be averaged but are useful for reference
        if input_dirs:
            first_run = input_dirs[0]
            for filename in ['index.html', 'tables_norm.txt', f'raw_results_{client}.csv']:
                src = os.path.join(first_run, filename)
                dst = os.path.join(output_dir, filename)
                if os.path.exists(src) and not os.path.exists(dst):
                    import shutil
                    shutil.copy(src, dst)
                    print(f"Copied {filename} from first run")


def main():
    parser = argparse.ArgumentParser(description='Average benchmark reports across runs')
    parser.add_argument('--input-pattern', required=True,
                        help='Glob pattern for input directories (e.g., "reports_a_run*")')
    parser.add_argument('--output', required=True,
                        help='Output directory for averaged reports')
    parser.add_argument('--clients', required=True,
                        help='Comma-separated list of clients')

    args = parser.parse_args()

    # Find input directories matching pattern
    input_dirs = sorted(glob.glob(args.input_pattern))
    if not input_dirs:
        print(f"No directories found matching pattern: {args.input_pattern}")
        return 1

    print(f"Found {len(input_dirs)} run directories: {input_dirs}")

    clients = [c.strip() for c in args.clients.split(',')]
    print(f"Processing clients: {clients}")

    average_reports(input_dirs, args.output, clients)
    return 0


if __name__ == '__main__':
    exit(main())
