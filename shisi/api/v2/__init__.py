"""API v2 路由汇总"""

from fastapi import APIRouter

from .character_routes import router as character_router
from .health_routes import router as health_router
from .migration_routes import router as migration_router
from .persona_routes import router as persona_router

v2_router = APIRouter()
v2_router.include_router(character_router)
v2_router.include_router(persona_router)
v2_router.include_router(migration_router)
v2_router.include_router(health_router)

__all__ = ["v2_router"]
