from typing import Any

from pydantic import BaseModel


class ApiResponse(BaseModel):
    success: bool = True
    data: Any = None
    message: str = ""


def ok(data=None, message: str = "") -> dict:
    return {"success": True, "data": data, "message": message}

