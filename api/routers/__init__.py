from api.routers.character_routes import router as character_router  # noqa: F401
from api.routers.voice_routes import router as voice_router  # noqa: F401
from api.routers.persona_card_routes import router as persona_card_router  # noqa: F401

__all__ = [
    "character_router",
    "voice_router",
    "persona_card_router",
]
