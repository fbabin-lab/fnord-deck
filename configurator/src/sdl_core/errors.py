from typing import Any


class SdlError(Exception):
    """Safe, structured error; never embed command environments or tracebacks."""

    def __init__(self, code: str, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def rpc(self, number: int = -32000) -> dict:
        return {
            "code": number,
            "message": self.message,
            "data": {"code": self.code, "messageKey": self.code.lower(), "details": self.details},
        }
