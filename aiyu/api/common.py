from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ApiResponse(BaseModel):
    data: Any = None
    error: Optional[str] = None
    trace_id: Optional[str] = None
