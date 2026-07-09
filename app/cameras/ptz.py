"""
ONVIF PTZ control. Uses onvif-zeep-async for the actual SOAP calls once a
device's ONVIF service address (host/port) is known (either entered
manually or found via app/cameras/onvif_discovery.py).

Continuous-move commands (pan/tilt/zoom directions) are auto-stopped after
a short duration server-side as a safety net in case the browser never
sends the corresponding "stop" (e.g. the user closes the tab mid-drag).
"""
from __future__ import annotations

import asyncio
import logging

from onvif import ONVIFCamera

from app.cameras.crypto import decrypt
from app.models import Camera

logger = logging.getLogger("pi_nvr.ptz")

DEFAULT_SPEED = 0.5
AUTO_STOP_SECONDS = 1.5  # continuous-move safety timeout

DIRECTIONS = {
    "up": (0.0, 1.0, 0.0),
    "down": (0.0, -1.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
    "right": (1.0, 0.0, 0.0),
    "zoom_in": (0.0, 0.0, 1.0),
    "zoom_out": (0.0, 0.0, -1.0),
}


class PTZUnsupportedError(RuntimeError):
    pass


async def _get_camera_client(camera: Camera) -> ONVIFCamera:
    if not camera.onvif_host:
        raise PTZUnsupportedError(f"Camera {camera.id} has no ONVIF host configured")
    password = decrypt(camera.onvif_password_enc) or ""
    client = ONVIFCamera(
        camera.onvif_host,
        camera.onvif_port or 80,
        camera.onvif_username or "",
        password,
    )
    await client.update_xaddrs()
    return client


async def _get_profile_token(client: ONVIFCamera) -> str:
    media_service = await client.create_media_service()
    profiles = await media_service.GetProfiles()
    if not profiles:
        raise PTZUnsupportedError("Camera reports no media profiles")
    return profiles[0].token


async def move(camera: Camera, direction: str, speed: float = DEFAULT_SPEED) -> None:
    if direction not in DIRECTIONS:
        raise ValueError(f"Unknown PTZ direction: {direction}")
    pan, tilt, zoom = DIRECTIONS[direction]
    client = await _get_camera_client(camera)
    ptz_service = await client.create_ptz_service()
    profile_token = await _get_profile_token(client)

    request = ptz_service.create_type("ContinuousMove")
    request.ProfileToken = profile_token
    request.Velocity = {
        "PanTilt": {"x": pan * speed, "y": tilt * speed},
        "Zoom": {"x": zoom * speed},
    }
    await ptz_service.ContinuousMove(request)
    logger.info("PTZ move: camera=%s direction=%s speed=%s", camera.id, direction, speed)

    async def _auto_stop():
        await asyncio.sleep(AUTO_STOP_SECONDS)
        try:
            await stop(camera)
        except Exception:  # noqa: BLE001
            pass

    asyncio.create_task(_auto_stop())


async def stop(camera: Camera) -> None:
    client = await _get_camera_client(camera)
    ptz_service = await client.create_ptz_service()
    profile_token = await _get_profile_token(client)
    request = ptz_service.create_type("Stop")
    request.ProfileToken = profile_token
    request.PanTilt = True
    request.Zoom = True
    await ptz_service.Stop(request)


async def go_home(camera: Camera) -> None:
    client = await _get_camera_client(camera)
    ptz_service = await client.create_ptz_service()
    profile_token = await _get_profile_token(client)
    request = ptz_service.create_type("GotoHomePosition")
    request.ProfileToken = profile_token
    await ptz_service.GotoHomePosition(request)


async def goto_preset(camera: Camera, preset_token: str) -> None:
    client = await _get_camera_client(camera)
    ptz_service = await client.create_ptz_service()
    profile_token = await _get_profile_token(client)
    request = ptz_service.create_type("GotoPreset")
    request.ProfileToken = profile_token
    request.PresetToken = preset_token
    await ptz_service.GotoPreset(request)


async def set_preset(camera: Camera, name: str) -> str:
    """Saves the camera's current position as a new preset and returns the
    ONVIF preset token so it can be stored in the PTZPreset table."""
    client = await _get_camera_client(camera)
    ptz_service = await client.create_ptz_service()
    profile_token = await _get_profile_token(client)
    request = ptz_service.create_type("SetPreset")
    request.ProfileToken = profile_token
    request.PresetName = name
    result = await ptz_service.SetPreset(request)
    return result  # ONVIF returns the new preset token


async def get_capabilities(camera: Camera) -> bool:
    """Returns True if the camera advertises PTZ support at all."""
    try:
        client = await _get_camera_client(camera)
        media_service = await client.create_media_service()
        profiles = await media_service.GetProfiles()
        return any(getattr(p, "PTZConfiguration", None) is not None for p in profiles)
    except Exception:  # noqa: BLE001
        return False
