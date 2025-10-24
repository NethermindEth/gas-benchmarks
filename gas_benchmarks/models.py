from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(slots=True)
class SectionData:
    """Represents a single measurement section from an Influx-style dump."""

    timestamp: int
    measurement: str
    tags: Dict[str, str]
    fields: Dict[str, str]


@dataclass(slots=True)
class RPCResponse:
    """Representation of a JSON-RPC response."""

    jsonrpc: str
    result: Any
    id: Any

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "RPCResponse":
        return RPCResponse(
            jsonrpc=data.get("jsonrpc"),
            result=data.get("result"),
            id=data.get("id"),
        )

    def get_result_status(self) -> Optional[str]:
        result = self.result
        if isinstance(result, dict):
            return result.get("status")
        return None


@dataclass(slots=True)
class PayloadResponse:
    """Representation of an Engine API payload response."""

    jsonrpc: str
    result: Any
    id: Any

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "PayloadResponse":
        return PayloadResponse(
            jsonrpc=data.get("jsonrpc"),
            result=data.get("result"),
            id=data.get("id"),
        )

    def get_payload_status(self) -> Optional[str]:
        result = self.result
        if isinstance(result, dict):
            payload_status = result.get("payloadStatus")
            if isinstance(payload_status, dict):
                return payload_status.get("status")
        return None

