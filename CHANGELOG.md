# Changelog

All notable changes to this project are documented in this file.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Network scan** as a fallback camera-finding method (Cameras page, next
  to "Discover (ONVIF)"). Many budget consumer cameras don't implement
  standard ONVIF WS-Discovery at all -- only a vendor's proprietary
  cloud/app-pairing protocol. This sweeps the Pi's own LAN subnet(s) for
  hosts with camera-typical ports open (RTSP 554, ONVIF 8899, HTTP 80/8080)
  so at least the camera's IP is confirmed, even when the exact stream
  path and credentials still have to come from the camera's app/manual.
  Automatically excludes the Tailscale interface and loopback.

### Fixed
- CI lint failures: removed unused imports (`os` in `storage/manager.py` and
  `tests/conftest.py`, `threading` in `camera_simulator.py`), replaced a
  lambda assignment with a proper `def` (PEP 8 E731), and raised flake8's
  line-length limit from 110 to 140 to match how FastAPI route signatures
  with multiple `Depends()` parameters actually look in this codebase,
  rather than fighting the style checker on every route.
- ONVIF discovery (`POST /api/cameras/discover`) crashed with a 500 error
  whenever any camera actually responded to the probe: it used
  `loop.sock_recv()`, which returns only the received bytes, but the code
  tried to unpack that into `(data, addr)` as if it were `sock_recvfrom()`.
  Fixed to call `sock_recvfrom()`, which actually returns both.

### Changed
- Removed the unused `nginx` dependency from `install.sh`. Pi-NVR is served
  directly by Uvicorn and never used nginx as a reverse proxy; leaving it
  installed only caused unnecessary port 80 conflicts with other services
  (e.g. AdGuard Home) that legitimately need that port.
- Replaced `passlib`'s bcrypt wrapper with a direct call to the `bcrypt`
  library. `passlib` has been unmaintained since 2020 and its backend
  detection breaks on modern `bcrypt` releases (removed `__about__`
  attribute), surfacing as a misleading "password cannot be longer than 72
  bytes" crash during admin account creation. Password length is now
  validated explicitly (max 72 bytes, bcrypt's real hard limit) at the API
  and `create_admin.py` before hashing, instead of failing deep inside a
  broken compatibility shim.

## [0.1.0] - Initial release

### Added
- FastAPI backend with session-cookie auth (bcrypt password hashing, no
  default credentials).
- Camera management: RTSP/ONVIF/MJPEG support, ONVIF WS-Discovery,
  encrypted credential storage.
- Recording engine: continuous, motion-triggered (ring-buffer pre/post
  record), scheduled, and manual modes, all stream-copy by default.
- OpenCV motion detection with include/exclude zones, sensitivity, min/max
  object size, and cooldown.
- ONVIF PTZ control: continuous move, stop, home, presets.
- Storage manager: mount browsing (local/USB/SSD/network), age- and
  usage-percent-based retention.
- Playback: calendar browser, range-seekable streaming, download, lock,
  delete, snapshots.
- Dashboard: CPU/RAM/disk/temperature, per-camera status, live WebSocket
  push, browser + email notifications.
- Dark-themed responsive web UI (no Node.js build step required).
- `install.sh` for Debian/Ubuntu/Raspberry Pi OS, installing a hardened
  systemd service.
- Pytest suite covering config, auth, recording command-building,
  retention, motion zone masking, and ONVIF response parsing.
- Plugin architecture stub for future AI detection / Home Assistant / MQTT
  / Telegram / Discord / cloud backup integrations.
