"""Route modules for the FastAPI server."""

from server.routes.artifacts import router as artifacts_router
from server.routes.attributes import router as attributes_router
from server.routes.capture import router as capture_router
from server.routes.interview import router as interview_router
from server.routes.preferences import router as preferences_router
from server.routes.query import router as query_router
from server.routes.setup import router as setup_router
from server.routes.session import router as session_router
from server.routes.teach import router as teach_router

__all__ = [
    "artifacts_router",
    "attributes_router",
    "capture_router",
    "interview_router",
    "preferences_router",
    "query_router",
    "setup_router",
    "session_router",
    "teach_router",
]
