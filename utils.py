"""
Legacy compatibility module.

Historically the project relied on a large ``utils.py`` file.  The functionality
has been reorganised under the ``gas_benchmarks`` package, but callers still
import ``utils`` directly.  This module re-exports the public helpers so that
existing scripts keep working while benefiting from the refactor.
"""

from gas_benchmarks.models import PayloadResponse, RPCResponse, SectionData
from gas_benchmarks.merge import merge_csv, merge_html
from gas_benchmarks.reporting import calculate_percentiles, get_gas_table
from gas_benchmarks.results import (
    check_client_response_is_valid,
    check_sync_status,
    extract_response_and_result,
    get_test_cases,
    iter_response_files,
    read_results,
)
from gas_benchmarks.system import convert_dotnet_ticks_to_utc, print_computer_specs

__all__ = [
    "PayloadResponse",
    "RPCResponse",
    "SectionData",
    "merge_csv",
    "merge_html",
    "calculate_percentiles",
    "get_gas_table",
    "check_client_response_is_valid",
    "check_sync_status",
    "extract_response_and_result",
    "get_test_cases",
    "iter_response_files",
    "read_results",
    "convert_dotnet_ticks_to_utc",
    "print_computer_specs",
]

