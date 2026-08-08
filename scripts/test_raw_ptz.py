#!/usr/bin/env python3
"""
Raw SOAP PTZ fallback validator.

Standard ONVIF capability negotiation (GetCapabilities/update_xaddrs) is
confirmed broken on at least one camera used during Pi-NVR development
(Jooan W5-U-US, port 8899) -- see scripts/onvif_probe.py, which will
hang or fail against it. app/cameras/onvif_raw.py works around this by
skipping negotiation and guessing at conventional ONVIF service paths.

This script exercises that fallback directly against real hardware,
step by step, so a failure at any stage tells you exactly where the
guessed paths/protocol assumptions broke down rather than just "PTZ
doesn't work". Run this BEFORE trusting the automatic fallback wired
into app/cameras/ptz.py in the live app.

Usage:
    /opt/pi-nvr/venv/bin/python3 scripts/test_raw_ptz.py \\
        --host 192.168.1.179 --port 8899 --username admin --password admin123

    # Skip the actual move/stop (profile+path resolution only, camera
    # won't physically move):
    ... --dry-run

WARNING: without --dry-run this will physically move the camera briefly
(a small pan-right nudge, auto-stopped after ~1s) to confirm ContinuousMove
actually works end-to-end, not just that a plausible-looking response came
back.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def run(host: str, port: int, username: str, password: str, dry_run: bool, zoom: bool = False) -> int:
    from app.cameras import onvif_raw

    base_url = f"http://{host}:{port}"
    print(f"Target: {base_url}\n")

    # Step 1: media service path resolution + GetProfiles
    print("[1/4] Resolving media service path and fetching profile token...")
    try:
        profile_token = await onvif_raw.get_profile_token(host, port, username, password)
        print(f"      OK -- profile token: {profile_token}")
    except onvif_raw.RawPTZError as exc:
        print(f"      FAILED: {exc}")
        print("\nCould not get a media profile token. Things to check:")
        print("  - Is the ONVIF port actually reachable? (curl -v http://%s:%d/)" % (host, port))
        print("  - Try adding custom paths to MEDIA_SERVICE_PATHS in onvif_raw.py")
        print("  - Camera may need a different auth style than WS-Security digest")
        return 1

    # Step 2: PTZ service path resolution (via a harmless Stop probe)
    print("\n[2/4] Resolving PTZ service path...")
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            ptz_url = await onvif_raw._resolve_ptz_url(client, base_url, username, password, profile_token)
        print(f"      OK -- PTZ service at: {ptz_url}")
    except onvif_raw.RawPTZError as exc:
        print(f"      FAILED: {exc}")
        print("\nGot a profile token but no PTZ service path answered. Camera")
        print("may not expose PTZ over ONVIF at all even though it's a PTZ camera")
        print("(some expose PTZ only via a proprietary app protocol).")
        return 1

    # Step 3: raw_detect() convenience wrapper (what ptz.py actually calls)
    print("\n[3/4] Running raw_detect() (what the app itself calls to decide")
    print("      whether to show PTZ controls)...")
    detected = await onvif_raw.raw_detect(host, port, username, password)
    print(f"      raw_detect() -> {detected}")

    if dry_run:
        print("\n[4/4] Skipped (--dry-run): not sending an actual move/stop command.")
        print("\nDry run complete. Path resolution succeeded -- rerun without")
        print("--dry-run to confirm the camera actually responds to movement.")
        return 0

    # Step 4: actual move + stop
    print(f"\n[4/4] Sending a brief {'zoom-in' if zoom else 'pan-right'} nudge, then Stop...")
    try:
        if zoom:
            await onvif_raw.raw_move(host, port, username, password, pan=0.0, tilt=0.0, zoom=0.5)
        else:
            await onvif_raw.raw_move(host, port, username, password, pan=0.3, tilt=0.0, zoom=0.0)
        print("      ContinuousMove accepted (no SOAP fault). Waiting 1.5s...")
        await asyncio.sleep(1.5)
        await onvif_raw.raw_stop(host, port, username, password)
        print("      Stop accepted (or zero-velocity fallback succeeded).")
    except onvif_raw.RawPTZError as exc:
        print(f"      FAILED: {exc}")
        print("\nPath resolution worked but the actual move/stop command was")
        print("rejected. Check the camera's live view or PTZ log (if any) to")
        print("see whether it moved despite the SOAP fault, or didn't move at all.")
        return 1

    print("\nAll steps passed (the camera accepted the command with no SOAP")
    print("fault). Did the camera actually move/zoom?")
    print("  - If yes: confirmed working end-to-end on this hardware.")
    if zoom:
        print("  - If the command was accepted but nothing physically zoomed:")
        print("    this usually means the camera's PTZ profile has no zoom")
        print("    actuator/range configured -- i.e. a hardware/firmware")
        print("    limit, not something fixable from the ONVIF side. Check")
        print("    whether the camera's own app offers a physical zoom")
        print("    control (not just pinch-to-zoom digital crop in its live")
        print("    view, which isn't real camera movement) to confirm.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", required=True, help="Camera's ONVIF service IP")
    parser.add_argument("--port", type=int, default=8899, help="ONVIF port (default 8899, matches Jooan)")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--dry-run", action="store_true", help="Resolve paths/profile only, don't move the camera")
    parser.add_argument("--zoom", action="store_true", help="Test a zoom-in nudge instead of pan-right (step 4 only)")
    args = parser.parse_args()

    return asyncio.run(run(args.host, args.port, args.username, args.password, args.dry_run, zoom=args.zoom))


if __name__ == "__main__":
    raise SystemExit(main())
