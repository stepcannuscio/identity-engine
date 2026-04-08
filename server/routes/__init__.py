"""Route modules for the FastAPI server."""

from server.routes.attributes import router as attributes_router
from server.routes.capture import router as capture_router
from server.routes.query import router as query_router
from server.routes.session import router as session_router

__all__ = [
    "attributes_router",
    "capture_router",
    "query_router",
    "session_router",
]
