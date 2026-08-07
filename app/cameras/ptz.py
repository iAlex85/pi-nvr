"""
ONVIF PTZ control. Uses onvif-zeep-async for the actual SOAP calls once a
device's ONVIF service address (host/port) is known (either entered
manually or found via app/cameras/onvif_discovery.py).

Continuous-move commands (pan/tilt/zoom directions) are auto-stopped after
a short duration server-side as a safety net in case the browser never
sends the corresponding "stop" (e.g. the user closes the tab mid-drag).

Some camera firmware fails standard ONVIF capability negotiation
(GetCapabilities/update_xaddrs) entirely -- confirmed on a Jooan
W5-U-US used during development. For those cameras every function below
falls back automatically to app/cameras/onvif_raw.py, a hand-rolled SOAP
client that skips negotiation and posts directly to conventional ONVIF
service paths. The fallback is best-effort and only used when the
standard path raises; see onvif_raw.py for details and caveats.
"""
from __future__ import annotations

import asyncio
import logging

from onvif import ONVIFCamera

from app.cameras import onvif_raw
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


def _raw_creds(camera: Camera) -> tuple[str, int, str, str]:
    if not camera.onvif_host:
        raise PTZUnsupportedError(f"Camera {camera.id} has no ONVIF host configured")
    password = decrypt(camera.onvif_password_enc) or ""
    return camera.onvif_host, camera.onvif_port or 80, camera.onvif_username or "", password


async def move(camera: Camera, direction: str, speed: float = DEFAULT_SPEED) -> None:
    if direction not in DIRECTIONS:
        raise ValueError(f"Unknown PTZ direction: {direction}")
    pan, tilt, zoom = DIRECTIONS[direction]

    try:
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
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "PTZ move: standard ONVIF failed for camera=%s (%s), trying raw SOAP fallback",
            camera.id, exc,
        )
        host, port, username, password = _raw_creds(camera)
        await onvif_raw.raw_move(host, port, username, password, pan * speed, tilt * speed, zoom * speed)

    logger.info("PTZ move: camera=%s direction=%s speed=%s", camera.id, direction, speed)

    async def _auto_stop():
        await asyncio.sleep(AUTO_STOP_SECONDS)
        try:
            await stop(camera)
        except Exception:  # noqa: BLE001
            pass

    asyncio.create_task(_auto_stop())


async def stop(camera: Camera) -> None:
    try:
        client = await _get_camera_client(camera)
        ptz_service = await client.create_ptz_service()
        profile_token = await _get_profile_token(client)
        request = ptz_service.create_type("Stop")
        request.ProfileToken = profile_token
        request.PanTilt = True
        request.Zoom = True
        await ptz_service.Stop(request)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "PTZ stop: standard ONVIF failed for camera=%s (%s), trying raw SOAP fallback",
            camera.id, exc,
        )
        host, port, username, password = _raw_creds(camera)
        await onvif_raw.raw_stop(host, port, username, password)


async def go_home(camera: Camera) -> None:
    try:
        client = await _get_camera_client(camera)
        ptz_service = await client.create_ptz_service()
        profile_token = await _get_profile_token(client)
        request = ptz_service.create_type("GotoHomePosition")
        request.ProfileToken = profile_token
        await ptz_service.GotoHomePosition(request)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "PTZ go_home: standard ONVIF failed for camera=%s (%s), trying raw SOAP fallback",
            camera.id, exc,
        )
        host, port, username, password = _raw_creds(camera)
        await onvif_raw.raw_go_home(host, port, username, password)


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


async def get_capabilities(camera: Camera) -> dict:
    """Returns {"supported": bool, "detail": str}. Previously this was a
    flat bool, which meant a genuinely-unsupported camera, an empty ONVIF
    host field, and a raw-fallback path guess that just didn't match this
    firmware all produced the identical "not supported" message -- no way
    to tell them apart without SSHing in and reading logs. The detail
    string pins down which stage actually failed so that's diagnosable
    from the UI alone."""
    if not camera.onvif_host:
        return {
            "supported": False,
            "detail": "No ONVIF host/port configured on this camera -- add "
                      "them in the camera's edit form (ONVIF Host/Port/"
                      "Username/Password fields) before checking for PTZ.",
        }

    try:
        client = await _get_camera_client(camera)
        media_service = await client.create_media_service()
        profiles = await media_service.GetProfiles()
        supported = any(getattr(p, "PTZConfiguration", None) is not None for p in profiles)
        return {
            "supported": supported,
            "detail": (
                "Detected via standard ONVIF." if supported
                else "Standard ONVIF connected, but the camera reports no "
                     "PTZ configuration on any media profile."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "PTZ get_capabilities: standard ONVIF failed for camera=%s (%s), trying raw SOAP fallback",
            camera.id, exc,
        )
        standard_error = str(exc) or type(exc).__name__

        try:
            host, port, username, password = _raw_creds(camera)
        except PTZUnsupportedError as raw_exc:
            return {"supported": False, "detail": f"Standard ONVIF failed ({standard_error}); {raw_exc}"}

        try:
            profile_token = await onvif_raw.get_profile_token(host, port, username, password)
        except onvif_raw.RawPTZError as raw_exc:
            return {
                "supported": False,
                "detail": (
                    f"Standard ONVIF failed ({standard_error}). Raw SOAP "
                    f"fallback also failed getting a media profile: {raw_exc}. "
                    "This usually means the guessed service paths in "
                    "onvif_raw.py don't match this camera's firmware, or "
                    "the ONVIF host/port/credentials are wrong."
                ),
            }

        import httpx
        try:
            async with httpx.AsyncClient() as raw_client:
                await onvif_raw._resolve_ptz_url(
                    raw_client, f"http://{host}:{port}", username, password, profile_token
                )
        except onvif_raw.RawPTZError as raw_exc:
            return {
                "supported": False,
                "detail": (
                    f"Standard ONVIF failed ({standard_error}). Raw SOAP "
                    f"fallback got a media profile token but no PTZ service "
                    f"path answered: {raw_exc}."
                ),
            }

        return {
            "supported": True,
            "detail": "Detected via raw SOAP fallback (standard ONVIF "
                      "capability negotiation failed on this firmware).",
        }
