from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ApiResponse(BaseModel):
    data: Any = None
    error: str | None = None
    trace_id: str | None = None
