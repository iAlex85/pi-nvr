"""
Plugin architecture. A plugin is a Python module under `plugins.enabled`
in config.yaml, exposing a subclass of `PiNVRPlugin`. Plugins are loaded
once at startup (see `load_enabled_plugins`) and get hooks into the
motion/recording event stream without needing to modify core code.

Intended future plugins (not built here, but this is the extension point
for them): AI object detection, license plate recognition, face
detection, Home Assistant integration, MQTT, Telegram, Discord, cloud
backup.
"""
from __future__ import annotations

import importlib
import logging

logger = logging.getLogger("pi_nvr.plugins")


class PiNVRPlugin:
    """Subclass this and implement whichever hooks you need; all are
    optional no-ops by default."""

    name: str = "unnamed-plugin"

    def __init__(self, cfg):
        self.cfg = cfg

    async def on_startup(self, app) -> None:
        """Called once during app startup, after core services are ready."""

    async def on_shutdown(self, app) -> None:
        """Called once during app shutdown, before core services stop."""

    async def on_motion_event(self, camera_id: int, score: float, bbox: tuple) -> None:
        """Called whenever MotionSupervisor detects motion, after the
        MotionEvent row has been written."""

    async def on_recording_started(self, camera_id: int, file_path: str) -> None:
        """Called when a new recording segment begins."""

    async def on_camera_status_changed(self, camera_id: int, online: bool) -> None:
        """Called when a camera transitions online <-> offline."""


def load_enabled_plugins(cfg) -> list[PiNVRPlugin]:
    """Imports and instantiates every plugin listed in `plugins.enabled`.
    Expects each entry to be an importable module path exposing a `Plugin`
    class, e.g. `plugins.enabled: ["pi_nvr_plugins.mqtt_bridge"]`."""
    plugins: list[PiNVRPlugin] = []
    for module_path in cfg.get("plugins.enabled", []):
        try:
            module = importlib.import_module(module_path)
            plugin_cls = getattr(module, "Plugin")
            plugins.append(plugin_cls(cfg))
            logger.info("Loaded plugin: %s", module_path)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to load plugin '%s'", module_path)
    return plugins
