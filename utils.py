import cpuinfo
import platform
import psutil


def print_computer_specs():
    info = "Computer Specs:\n"
    cpu = cpuinfo.get_cpu_info()
    system_info = {
        'Processor': platform.processor(),
        'System': platform.system(),
        'Release': platform.release(),
        'Version': platform.version(),
        'Machine': platform.machine(),
        'Processor Architecture': platform.architecture()[0],
        'RAM': f'{psutil.virtual_memory().total / (1024 ** 3):.2f} GB',
        'CPU': cpu['brand_raw'],
        'Numbers of CPU': cpu['count'],
        'CPU GHz': cpu['hz_actual_friendly']
    }

    # Print the specifications
    for key, value in system_info.items():
        line = f'{key}: {value}'
        print(line)
        info += line + "\n"
    return info + "\n"

