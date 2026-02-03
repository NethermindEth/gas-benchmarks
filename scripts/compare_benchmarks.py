#!/usr/bin/env python3
"""
Compare two benchmark runs and generate a comparison report.

Usage:
    python compare_benchmarks.py \
        --reports-a reports_a \
        --reports-b reports_b \
        --output reports_comparison \
        --clients nethermind \
        --label-a "Baseline" \
        --label-b "New Version"
"""

import argparse
import csv
import os
from pathlib import Path
from typing import Optional


# Metrics where higher is better (MGas/s)
HIGHER_IS_BETTER = {
    'Max (MGas/s)',
    'p50 (MGas/s)',
    'p95 (MGas/s)',
    'p99 (MGas/s)',
    'Min (MGas/s)',
}

# Metrics where lower is better (time in ms)
LOWER_IS_BETTER = {
    'Duration (ms)',
    'FCU time (ms)',
    'NP time (ms)',
}

# All metrics to compare
ALL_METRICS = list(HIGHER_IS_BETTER) + list(LOWER_IS_BETTER)


def load_benchmark_csv(csv_path: str) -> dict:
    """Load benchmark CSV and return dict keyed by Title."""
    results = {}
    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = row.get('Title')
            if title:
                results[title] = row
    return results


def safe_float(val) -> Optional[float]:
    """Safely convert to float."""
    if val is None or val == '':
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def calculate_delta(a_val: Optional[float], b_val: Optional[float]) -> tuple:
    """Calculate absolute and percentage delta."""
    if a_val is None or b_val is None:
        return None, None
    if a_val == 0:
        return b_val - a_val, None  # Can't calculate percentage
    delta = b_val - a_val
    pct = ((b_val - a_val) / abs(a_val)) * 100
    return delta, pct


def format_val(val: Optional[float]) -> str:
    """Format value for display."""
    if val is None:
        return "N/A"
    return f"{val:.2f}"


def format_delta(delta: Optional[float], pct: Optional[float], higher_is_better: bool = True) -> str:
    """Format delta with indicator."""
    if delta is None:
        return "N/A"

    # No indicator for zero delta
    if abs(delta) < 0.001:
        return "0.00 (0.0%)"

    # Determine if this is an improvement
    is_improvement = (delta > 0) if higher_is_better else (delta < 0)

    sign = "+" if delta > 0 else ""
    indicator = "^" if is_improvement else "v"

    if pct is not None:
        return f"{sign}{delta:.2f} ({pct:+.1f}%) {indicator}"
    return f"{sign}{delta:.2f} {indicator}"


def truncate_title(title: str, max_len: int = 60) -> str:
    """Truncate title for readability."""
    if len(title) <= max_len:
        return title
    return title[:max_len - 3] + "..."


def generate_comparison_table(data_a: dict, data_b: dict,
                               label_a: str, label_b: str,
                               client: str) -> str:
    """Generate markdown comparison table."""
    lines = []
    lines.append(f"## {client.capitalize()} Comparison: {label_a} vs {label_b}")
    lines.append("")

    # Header - focus on key metrics
    lines.append("| Test | p50 A | p50 B | p50 Delta | p95 A | p95 B | p95 Delta | Max A | Max B | Max Delta |")
    lines.append("|------|-------|-------|-----------|-------|-------|-----------|-------|-------|-----------|")

    # Match tests by title
    all_titles = sorted(set(data_a.keys()) | set(data_b.keys()))

    for title in all_titles:
        row_a = data_a.get(title, {})
        row_b = data_b.get(title, {})

        # Extract metrics (higher is better for MGas/s)
        p50_a = safe_float(row_a.get('p50 (MGas/s)'))
        p50_b = safe_float(row_b.get('p50 (MGas/s)'))
        p95_a = safe_float(row_a.get('p95 (MGas/s)'))
        p95_b = safe_float(row_b.get('p95 (MGas/s)'))
        max_a = safe_float(row_a.get('Max (MGas/s)'))
        max_b = safe_float(row_b.get('Max (MGas/s)'))

        p50_delta, p50_pct = calculate_delta(p50_a, p50_b)
        p95_delta, p95_pct = calculate_delta(p95_a, p95_b)
        max_delta, max_pct = calculate_delta(max_a, max_b)

        short_title = truncate_title(title)

        lines.append(
            f"| {short_title} | "
            f"{format_val(p50_a)} | {format_val(p50_b)} | {format_delta(p50_delta, p50_pct, True)} | "
            f"{format_val(p95_a)} | {format_val(p95_b)} | {format_delta(p95_delta, p95_pct, True)} | "
            f"{format_val(max_a)} | {format_val(max_b)} | {format_delta(max_delta, max_pct, True)} |"
        )

    lines.append("")
    lines.append("**Legend:** ^ = improvement, v = regression (for MGas/s: higher is better)")
    lines.append("")

    return "\n".join(lines)


def generate_duration_table(data_a: dict, data_b: dict,
                            label_a: str, label_b: str,
                            client: str) -> str:
    """Generate markdown table for duration metrics."""
    lines = []
    lines.append(f"### {client.capitalize()} Duration Metrics")
    lines.append("")

    lines.append("| Test | Duration A | Duration B | Delta | FCU A | FCU B | Delta | NP A | NP B | Delta |")
    lines.append("|------|------------|------------|-------|-------|-------|-------|------|------|-------|")

    all_titles = sorted(set(data_a.keys()) | set(data_b.keys()))

    for title in all_titles:
        row_a = data_a.get(title, {})
        row_b = data_b.get(title, {})

        dur_a = safe_float(row_a.get('Duration (ms)'))
        dur_b = safe_float(row_b.get('Duration (ms)'))
        fcu_a = safe_float(row_a.get('FCU time (ms)'))
        fcu_b = safe_float(row_b.get('FCU time (ms)'))
        np_a = safe_float(row_a.get('NP time (ms)'))
        np_b = safe_float(row_b.get('NP time (ms)'))

        dur_delta, dur_pct = calculate_delta(dur_a, dur_b)
        fcu_delta, fcu_pct = calculate_delta(fcu_a, fcu_b)
        np_delta, np_pct = calculate_delta(np_a, np_b)

        short_title = truncate_title(title)

        lines.append(
            f"| {short_title} | "
            f"{format_val(dur_a)} | {format_val(dur_b)} | {format_delta(dur_delta, dur_pct, False)} | "
            f"{format_val(fcu_a)} | {format_val(fcu_b)} | {format_delta(fcu_delta, fcu_pct, False)} | "
            f"{format_val(np_a)} | {format_val(np_b)} | {format_delta(np_delta, np_pct, False)} |"
        )

    lines.append("")
    lines.append("**Legend:** ^ = improvement, v = regression (for duration: lower is better)")
    lines.append("")

    return "\n".join(lines)


def write_comparison_csv(data_a: dict, data_b: dict, client: str,
                         output_dir: str, label_a: str, label_b: str):
    """Write detailed comparison CSV file."""
    csv_path = os.path.join(output_dir, f'comparison_{client}.csv')

    all_titles = sorted(set(data_a.keys()) | set(data_b.keys()))

    headers = ['Title']
    for metric in ALL_METRICS:
        metric_name = metric.replace(' (MGas/s)', '').replace(' (ms)', '')
        headers.extend([
            f'{metric_name} {label_a}',
            f'{metric_name} {label_b}',
            f'{metric_name} Delta',
            f'{metric_name} Delta %',
        ])

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        for title in all_titles:
            row_a = data_a.get(title, {})
            row_b = data_b.get(title, {})

            row = [title]
            for metric in ALL_METRICS:
                val_a = safe_float(row_a.get(metric))
                val_b = safe_float(row_b.get(metric))
                delta, pct = calculate_delta(val_a, val_b)

                row.extend([
                    format_val(val_a) if val_a is not None else '',
                    format_val(val_b) if val_b is not None else '',
                    f'{delta:.2f}' if delta is not None else '',
                    f'{pct:.2f}' if pct is not None else '',
                ])

            writer.writerow(row)

    print(f"Written comparison CSV to {csv_path}")


def write_github_summary(content: str, summary_file: str = None):
    """Write content to GitHub Actions summary."""
    if summary_file is None:
        summary_file = os.environ.get('GITHUB_STEP_SUMMARY')

    if summary_file:
        with open(summary_file, 'a') as f:
            f.write(content + "\n\n")

    # Also print to stdout
    print(content)


def main():
    parser = argparse.ArgumentParser(description='Compare benchmark results')
    parser.add_argument('--reports-a', required=True, help='Path to baseline reports')
    parser.add_argument('--reports-b', required=True, help='Path to comparison reports')
    parser.add_argument('--output', default='reports_comparison', help='Output directory')
    parser.add_argument('--clients', required=True, help='Comma-separated client list')
    parser.add_argument('--label-a', default='Baseline', help='Label for first run')
    parser.add_argument('--label-b', default='Comparison', help='Label for second run')

    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    full_summary = []
    full_summary.append("# Gas Benchmark Comparison Results")
    full_summary.append("")

    for client in args.clients.split(','):
        client = client.strip()
        csv_a = os.path.join(args.reports_a, f'output_{client}.csv')
        csv_b = os.path.join(args.reports_b, f'output_{client}.csv')

        if not os.path.exists(csv_a):
            full_summary.append(f"### {client}: Missing baseline data ({csv_a})")
            full_summary.append("")
            continue

        if not os.path.exists(csv_b):
            full_summary.append(f"### {client}: Missing comparison data ({csv_b})")
            full_summary.append("")
            continue

        data_a = load_benchmark_csv(csv_a)
        data_b = load_benchmark_csv(csv_b)

        if not data_a and not data_b:
            full_summary.append(f"### {client}: No data in either report")
            full_summary.append("")
            continue

        # Generate throughput comparison table
        table = generate_comparison_table(data_a, data_b, args.label_a, args.label_b, client)
        full_summary.append(table)

        # Generate duration comparison table
        duration_table = generate_duration_table(data_a, data_b, args.label_a, args.label_b, client)
        full_summary.append(duration_table)

        # Write per-client comparison CSV
        write_comparison_csv(data_a, data_b, client, args.output, args.label_a, args.label_b)

    summary_content = "\n".join(full_summary)
    write_github_summary(summary_content)

    # Also save to file
    summary_path = os.path.join(args.output, 'comparison_summary.md')
    with open(summary_path, 'w') as f:
        f.write(summary_content)

    print(f"\nComparison summary saved to {summary_path}")


if __name__ == '__main__':
    main()
