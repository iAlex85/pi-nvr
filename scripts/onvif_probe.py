#!/usr/bin/env python3
"""
ONVIF stream URI probe.

Queries a camera's ONVIF service directly for its actual RTSP stream
URL(s), instead of guessing paths like /onvif1, /live/ch00_0, etc. Useful
whenever a camera answers ONVIF discovery (see app/cameras/onvif_discovery.py
or the "Discover ONVIF" button in the UI) but its RTSP path/credentials
aren't documented anywhere accessible.

Usage:
    /opt/pi-nvr/venv/bin/python3 scripts/onvif_probe.py \\
        --host 192.168.1.179 --port 8899 --username admin --password admin123

If the given credentials are wrong, ONVIF will fail with an auth error at
the GetProfiles step -- that alone is useful information (confirms the
port/protocol is right, narrows the problem down to credentials only).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def probe(host: str, port: int, username: str, password: str) -> int:
    from onvif import ONVIFCamera

    print(f"Connecting to ONVIF service at {host}:{port} ...")
    camera = ONVIFCamera(host, port, username, password)
    try:
        await camera.update_xaddrs()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to connect/authenticate: {exc}")
        return 1

    print("Connected. Fetching device info...")
    try:
        device_service = await camera.create_devicemgmt_service()
        info = await device_service.GetDeviceInformation()
        print(f"  Manufacturer: {getattr(info, 'Manufacturer', '?')}")
        print(f"  Model:        {getattr(info, 'Model', '?')}")
        print(f"  Firmware:     {getattr(info, 'FirmwareVersion', '?')}")
    except Exception as exc:  # noqa: BLE001
        print(f"  (could not fetch device info: {exc})")

    print("\nFetching media profiles and stream URIs...")
    media_service = await camera.create_media_service()
    profiles = await media_service.GetProfiles()

    if not profiles:
        print("No media profiles reported -- unusual, but nothing more to probe.")
        return 1

    for profile in profiles:
        print(f"\n--- Profile: {profile.Name} (token={profile.token}) ---")
        try:
            request = media_service.create_type("GetStreamUri")
            request.ProfileToken = profile.token
            request.StreamSetup = {
                "Stream": "RTP-Unicast",
                "Transport": {"Protocol": "RTSP"},
            }
            stream_uri = await media_service.GetStreamUri(request)
            print(f"  RTSP URL: {stream_uri.Uri}")
        except Exception as exc:  # noqa: BLE001
            print(f"  Could not get stream URI for this profile: {exc}")

        resolution = getattr(getattr(profile, "VideoEncoderConfiguration", None), "Resolution", None)
        if resolution:
            print(f"  Resolution: {resolution.Width}x{resolution.Height}")

    print(
        "\nDone. Use the RTSP URL(s) above directly in Pi-NVR's 'Add camera' "
        "form -- credentials go in the separate Username/Password fields, "
        "not embedded in the URL."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Camera's ONVIF service IP")
    parser.add_argument("--port", type=int, default=80, help="ONVIF port (often 80, 8080, or 8899)")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    return asyncio.run(probe(args.host, args.port, args.username, args.password))


if __name__ == "__main__":
    raise SystemExit(main())
