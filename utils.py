import cpuinfo
import platform
import psutil


class SectionData:
    def __init__(self, timestamp, measurement, tags, fields):
        self.timestamp = timestamp
        self.measurement = measurement
        self.tags = tags
        self.fields = fields

    def __repr__(self):
        return f"SectionData(timestamp={self.timestamp}, measurement='{self.measurement}', tags={self.tags}, " \
               f"fields={self.fields})"


class RPCResponse:
    def __init__(self, jsonrpc, result, id):
        self.jsonrpc = jsonrpc
        self.result = result
        self.id = id

    def __repr__(self):
        return f"RPCResponse(jsonrpc={self.jsonrpc}, result={self.result}, id={self.id})"

    @staticmethod
    def from_dict(data):
        jsonrpc = data.get("jsonrpc")
        result = data.get("result")
        id = data.get("id")
        return RPCResponse(jsonrpc, result, id)

    def get_result_status(self):
        if self.result and "status" in self.result:
            return self.result["status"]
        return None


class PayloadResponse:
    def __init__(self, jsonrpc, result, id):
        self.jsonrpc = jsonrpc
        self.result = result
        self.id = id

    def __repr__(self):
        return f"PayloadResponse(jsonrpc={self.jsonrpc}, result={self.result}, id={self.id})"

    @staticmethod
    def from_dict(data):
        jsonrpc = data.get("jsonrpc")
        result = data.get("result")
        id = data.get("id")
        return PayloadResponse(jsonrpc, result, id)

    def get_payload_status(self):
        if self.result and "payloadStatus" in self.result and "status" in self.result["payloadStatus"]:
            return self.result["payloadStatus"]["status"]
        return None


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

