#!/usr/bin/env python3
"""
Compare two benchmark runs and generate a comparison report.

Usage:
    python compare_benchmarks.py \
        --reports-a reports_a \
        --reports-b reports_b \
        --output reports_comparison \
        --clients nethermind \
        --label-a "v1.36.0" \
        --label-b "v1.35.8" \
        --metrics "min,max,duration"
"""

import argparse
import csv
import os
from typing import Optional, List


# Metric configuration: name -> (csv_column, short_name, higher_is_better)
METRIC_CONFIG = {
    'min': ('Min (MGas/s)', 'Min', True),
    'max': ('Max (MGas/s)', 'Max', True),
    'p50': ('p50 (MGas/s)', 'p50', True),
    'p95': ('p95 (MGas/s)', 'p95', True),
    'p99': ('p99 (MGas/s)', 'p99', True),
    'duration': ('Duration (ms)', 'Dur', False),
    'fcu': ('FCU time (ms)', 'FCU', False),
    'np': ('NP time (ms)', 'NP', False),
}

DEFAULT_METRICS = ['min', 'max', 'duration']

# All metrics for CSV export
ALL_METRICS = list(METRIC_CONFIG.keys())


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


def format_val(val: Optional[float], is_duration: bool = False) -> str:
    """Format value for display."""
    if val is None:
        return "N/A"
    if is_duration:
        return f"{val:.0f}"
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


def truncate_title(title: str, max_len: int = 0) -> str:
    """Truncate title for readability. If max_len is 0 or None, return full title."""
    if not max_len or max_len <= 0 or len(title) <= max_len:
        return title
    return title[:max_len - 3] + "..."


def generate_unified_table(data_a: dict, data_b: dict,
                           label_a: str, label_b: str,
                           client: str, metrics: List[str],
                           max_title_length: int = 0) -> str:
    """Generate a single unified markdown comparison table."""
    lines = []

    # Header with clear A/B labeling
    lines.append(f"## {client.capitalize()} Comparison: {label_a} (A) vs {label_b} (B)")
    lines.append("")

    # Build dynamic header based on selected metrics
    header_parts = ["| Test"]
    separator_parts = ["|------"]

    for metric_key in metrics:
        if metric_key not in METRIC_CONFIG:
            continue
        _, short_name, _ = METRIC_CONFIG[metric_key]
        header_parts.extend([f" {short_name} A", f" {short_name} B", f" {short_name} Delta"])
        separator_parts.extend(["-------", "-------", "-----------"])

    lines.append(" |".join(header_parts) + " |")
    lines.append(" |".join(separator_parts) + "|")

    # Match tests by title
    all_titles = sorted(set(data_a.keys()) | set(data_b.keys()))

    for title in all_titles:
        row_a = data_a.get(title, {})
        row_b = data_b.get(title, {})

        row_parts = [f"| {truncate_title(title, max_title_length)}"]

        for metric_key in metrics:
            if metric_key not in METRIC_CONFIG:
                continue
            csv_col, _, higher_is_better = METRIC_CONFIG[metric_key]
            is_duration = not higher_is_better

            val_a = safe_float(row_a.get(csv_col))
            val_b = safe_float(row_b.get(csv_col))
            delta, pct = calculate_delta(val_a, val_b)

            row_parts.extend([
                f" {format_val(val_a, is_duration)}",
                f" {format_val(val_b, is_duration)}",
                f" {format_delta(delta, pct, higher_is_better)}"
            ])

        lines.append(" |".join(row_parts) + " |")

    lines.append("")
    lines.append("**Legend:** ^ = improvement, v = regression")

    # Build legend for metric types
    mgas_metrics = [METRIC_CONFIG[m][1] for m in metrics if m in METRIC_CONFIG and METRIC_CONFIG[m][2]]
    duration_metrics = [METRIC_CONFIG[m][1] for m in metrics if m in METRIC_CONFIG and not METRIC_CONFIG[m][2]]

    if mgas_metrics:
        lines.append(f"- For MGas/s ({', '.join(mgas_metrics)}): higher is better")
    if duration_metrics:
        lines.append(f"- For Duration ({', '.join(duration_metrics)}): lower is better")

    lines.append("")

    return "\n".join(lines)


def write_comparison_csv(data_a: dict, data_b: dict, client: str,
                         output_dir: str, label_a: str, label_b: str):
    """Write detailed comparison CSV file with all metrics."""
    csv_path = os.path.join(output_dir, f'comparison_{client}.csv')

    all_titles = sorted(set(data_a.keys()) | set(data_b.keys()))

    headers = ['Title']
    for metric_key in ALL_METRICS:
        csv_col, short_name, _ = METRIC_CONFIG[metric_key]
        headers.extend([
            f'{short_name} {label_a}',
            f'{short_name} {label_b}',
            f'{short_name} Delta',
            f'{short_name} Delta %',
        ])

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        for title in all_titles:
            row_a = data_a.get(title, {})
            row_b = data_b.get(title, {})

            row = [title]
            for metric_key in ALL_METRICS:
                csv_col, _, _ = METRIC_CONFIG[metric_key]
                val_a = safe_float(row_a.get(csv_col))
                val_b = safe_float(row_b.get(csv_col))
                delta, pct = calculate_delta(val_a, val_b)

                row.extend([
                    f'{val_a:.2f}' if val_a is not None else '',
                    f'{val_b:.2f}' if val_b is not None else '',
                    f'{delta:.2f}' if delta is not None else '',
                    f'{pct:.2f}' if pct is not None else '',
                ])

            writer.writerow(row)

    print(f"Written comparison CSV to {csv_path}")


def print_summary(content: str):
    """Print summary to stdout for logging."""
    print(content)


def parse_metrics(metrics_str: str) -> List[str]:
    """Parse comma-separated metrics string into list."""
    metrics = [m.strip().lower() for m in metrics_str.split(',')]
    valid_metrics = [m for m in metrics if m in METRIC_CONFIG]
    if not valid_metrics:
        print(f"Warning: No valid metrics found in '{metrics_str}', using defaults: {DEFAULT_METRICS}")
        return DEFAULT_METRICS
    return valid_metrics


def main():
    parser = argparse.ArgumentParser(description='Compare benchmark results')
    parser.add_argument('--reports-a', required=True, help='Path to baseline reports')
    parser.add_argument('--reports-b', required=True, help='Path to comparison reports')
    parser.add_argument('--output', default='reports_comparison', help='Output directory')
    parser.add_argument('--clients', required=True, help='Comma-separated client list')
    parser.add_argument('--label-a', default='Baseline', help='Label for first run')
    parser.add_argument('--label-b', default='Comparison', help='Label for second run')
    parser.add_argument('--metrics', default='min,max,duration',
                        help='Comma-separated metrics to display (available: min,max,p50,p95,p99,duration,fcu,np)')
    parser.add_argument('--max-title-length', type=int, default=0,
                        help='Maximum title length in table (0 = no truncation, default: 0)')

    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    metrics = parse_metrics(args.metrics)

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

        # Generate single unified comparison table
        table = generate_unified_table(data_a, data_b, args.label_a, args.label_b, client, metrics,
                                       args.max_title_length)
        full_summary.append(table)

        # Write per-client comparison CSV (always includes all metrics)
        write_comparison_csv(data_a, data_b, client, args.output, args.label_a, args.label_b)

    summary_content = "\n".join(full_summary)
    print_summary(summary_content)

    # Also save to file
    summary_path = os.path.join(args.output, 'comparison_summary.md')
    with open(summary_path, 'w') as f:
        f.write(summary_content)

    print(f"\nComparison summary saved to {summary_path}")


if __name__ == '__main__':
    main()
