"""Aggregates every domain router into a single `api_router` mounted at
/api in app/main.py. Keeping this as pure aggregation (no logic) makes it
trivial to see the whole API surface at a glance."""
from fastapi import APIRouter

from app.auth.routes import router as auth_router
from app.cameras.routes import router as cameras_router
from app.recording.routes import router as recording_router
from app.motion.routes import router as motion_router
from app.storage.routes import router as storage_router
from app.playback.routes import router as playback_router
from app.dashboard.routes import router as dashboard_router
from app.websocket.routes import router as websocket_router

api_router = APIRouter()

api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(cameras_router, prefix="/cameras", tags=["cameras"])
api_router.include_router(recording_router, prefix="/recordings", tags=["recording"])
api_router.include_router(motion_router, prefix="/motion", tags=["motion"])
api_router.include_router(storage_router, prefix="/storage", tags=["storage"])
api_router.include_router(playback_router, prefix="/playback", tags=["playback"])
api_router.include_router(dashboard_router, prefix="/system", tags=["dashboard"])
api_router.include_router(websocket_router, tags=["websocket"])
