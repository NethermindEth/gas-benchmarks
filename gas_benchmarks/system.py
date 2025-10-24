from __future__ import annotations

import datetime as _dt
import platform
from typing import Dict

import cpuinfo
import psutil


def _collect_specs() -> Dict[str, str]:
    cpu = cpuinfo.get_cpu_info()
    memory_gb = psutil.virtual_memory().total / (1024 ** 3)
    return {
        "Processor": platform.processor(),
        "System": platform.system(),
        "Release": platform.release(),
        "Version": platform.version(),
        "Machine": platform.machine(),
        "Processor Architecture": platform.architecture()[0],
        "RAM": f"{memory_gb:.2f} GB",
        "CPU": cpu.get("brand_raw", "Unknown CPU"),
        "Numbers of CPU": str(cpu.get("count", "")),
        "CPU GHz": cpu.get("hz_actual_friendly", "N/A"),
    }


def print_computer_specs() -> str:
    """
    Print the current host specifications and return the formatted string.
    """
    lines = ["Computer Specs:"]
    specs = _collect_specs()
    for key, value in specs.items():
        line = f"{key}: {value}"
        print(line)
        lines.append(line)
    lines.append("")  # maintain trailing newline
    return "\n".join(lines)


def convert_dotnet_ticks_to_utc(ticks: int) -> str:
    """
    Convert .NET ticks to an ISO-8601 timestamp in UTC.
    """
    dotnet_epoch = _dt.datetime(1, 1, 1, tzinfo=_dt.timezone.utc)
    seconds = ticks / 10_000_000
    dt = dotnet_epoch + _dt.timedelta(seconds=seconds)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f+00")

