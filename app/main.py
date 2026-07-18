"""
Pi-NVR application entrypoint.

Run with:
    uvicorn app.main:app --host 0.0.0.0 --port 8080

In production this is invoked by the systemd unit in systemd/pi-nvr.service,
which sets PI_NVR_CONFIG to point at /etc/pi-nvr/config.yaml.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_config
from app.database import init_db
from app.logging_setup import configure_logging
from app.api.router import api_router
from app.auth.dependencies import get_current_user_optional
from app.cameras.manager import CameraManager
from app.recording.engine import RecordingEngine
from app.motion.detector import MotionSupervisor
from app.storage.manager import StorageManager
from app.notifications.manager import NotificationManager
from app.websocket.manager import ConnectionManager

logger = logging.getLogger("pi_nvr")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    configure_logging(cfg)
    init_db()

    # Shared singletons, attached to app.state so routers can reach them via
    # `request.app.state.xyz` without a global-import spaghetti.
    app.state.config = cfg
    app.state.ws_manager = ConnectionManager()
    app.state.notifications = NotificationManager(cfg, app.state.ws_manager)
    app.state.storage = StorageManager(cfg)
    app.state.camera_manager = CameraManager(cfg)
    app.state.recording_engine = RecordingEngine(cfg, app.state.storage)
    app.state.camera_manager.set_recording_engine(app.state.recording_engine)
    app.state.motion_supervisor = MotionSupervisor(
        cfg, app.state.recording_engine, app.state.notifications
    )

    await app.state.camera_manager.start()
    await app.state.recording_engine.start(app.state.camera_manager)
    await app.state.motion_supervisor.start(app.state.camera_manager)
    await app.state.storage.start_retention_loop()

    logger.info("Pi-NVR startup complete")
    yield

    logger.info("Pi-NVR shutting down")
    await app.state.motion_supervisor.stop()
    await app.state.recording_engine.stop()
    await app.state.camera_manager.stop()
    app.state.storage.stop_retention_loop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Pi-NVR",
        description="Lightweight, self-hosted CCTV NVR for Raspberry Pi",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.mount(
        "/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static"
    )

    app.include_router(api_router, prefix="/api")

    @app.get("/")
    async def root(request: Request):
        user = await get_current_user_optional(request)
        if not user:
            return RedirectResponse(url="/login")
        return RedirectResponse(url="/dashboard")

    @app.get("/login")
    async def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {})

    @app.get("/dashboard")
    async def dashboard_page(request: Request):
        user = await get_current_user_optional(request)
        if not user:
            return RedirectResponse(url="/login")
        return templates.TemplateResponse(request, "dashboard.html", {"user": user})

    @app.get("/live")
    async def live_page(request: Request):
        user = await get_current_user_optional(request)
        if not user:
            return RedirectResponse(url="/login")
        return templates.TemplateResponse(request, "live.html", {"user": user})

    @app.get("/playback")
    async def playback_page(request: Request):
        user = await get_current_user_optional(request)
        if not user:
            return RedirectResponse(url="/login")
        return templates.TemplateResponse(request, "playback.html", {"user": user})

    @app.get("/cameras")
    async def cameras_page(request: Request):
        user = await get_current_user_optional(request)
        if not user:
            return RedirectResponse(url="/login")
        return templates.TemplateResponse(request, "cameras.html", {"user": user})

    @app.get("/storage")
    async def storage_page(request: Request):
        user = await get_current_user_optional(request)
        if not user:
            return RedirectResponse(url="/login")
        return templates.TemplateResponse(request, "storage.html", {"user": user})

    @app.get("/settings")
    async def settings_page(request: Request):
        user = await get_current_user_optional(request)
        if not user:
            return RedirectResponse(url="/login")
        return templates.TemplateResponse(request, "settings.html", {"user": user})

    return app


app = create_app()


if __name__ == "__main__":
    # Allows `python3 -m app.main` (used by systemd) to honor the
    # server.host/server.port/server.https_* settings from config.yaml,
    # rather than hardcoding them on a uvicorn command line where the
    # Settings > Network page's changes would silently have no effect.
    import uvicorn

    cfg = get_config()
    uvicorn_kwargs = {
        "host": cfg.get("server.host", "0.0.0.0"),
        "port": cfg.get("server.port", 8080),
    }
    if cfg.get("server.https_enabled", False):
        cert = cfg.get("server.https_cert", "")
        key = cfg.get("server.https_key", "")
        if cert and key:
            uvicorn_kwargs["ssl_certfile"] = cert
            uvicorn_kwargs["ssl_keyfile"] = key
        else:
            logger.warning("https_enabled is true but https_cert/https_key are not set; starting HTTP only")

    uvicorn.run("app.main:app", **uvicorn_kwargs)
