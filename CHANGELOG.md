# Changelog

All notable changes to this project are documented in this file.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- `scripts/onvif_probe.py`: queries a camera's ONVIF service directly for
  its real RTSP stream URI(s) via `GetStreamUri`, instead of guessing
  common path patterns. Useful for cameras that answer ONVIF discovery
  but whose RTSP path/credentials aren't documented anywhere accessible.
- **Network scan** as a fallback camera-finding method (Cameras page, next
  to "Discover (ONVIF)"). Many budget consumer cameras don't implement
  standard ONVIF WS-Discovery at all -- only a vendor's proprietary
  cloud/app-pairing protocol. This sweeps the Pi's own LAN subnet(s) for
  hosts with camera-typical ports open (RTSP 554, ONVIF 8899, HTTP 80/8080)
  so at least the camera's IP is confirmed, even when the exact stream
  path and credentials still have to come from the camera's app/manual.
  Automatically excludes the Tailscale interface and loopback.

### Fixed
- Live view stopped streaming after navigating away and back, on cameras
  that only accept one RTSP client at a time. The MJPEG live-view
  generator never actively checked whether the browser was still
  connected -- it only noticed passively, if at all, whenever it next
  tried to send a frame. Navigating away left the old `ffmpeg` process
  (and the one RTSP connection slot it held) running indefinitely in the
  background; returning to live view then spawned a second `ffmpeg`
  process that couldn't get a connection because the first one never let
  go. Now actively polls `request.is_disconnected()` each loop iteration
  (via a short read timeout so the check isn't blocked waiting on new
  frames) and terminates the process promptly once the client is gone.
- **Recording crash-looped forever, and could take down live view with
  it**, for any camera sending PCM A-law/mu-law audio (common on
  budget/consumer IP cameras): the default MP4 container flatly rejects
  those codecs even via stream-copy, so `ffmpeg` failed to write the file
  header on every single attempt, and Pi-NVR's auto-restart retried every
  few seconds indefinitely -- continuously cycling a new RTSP connection
  on cameras that only accept one client at a time, starving live view
  and motion detection of the connection they needed. Changed the default
  recording container from MP4 to **MKV**, which accepts arbitrary codecs
  via stream-copy with no such restriction. Also fixed `/api/playback/stream`
  and related endpoints, which hardcoded `video/mp4` as the response MIME
  type regardless of the recording's actual container -- now detected
  from the file extension, so MKV recordings are served with the correct
  type instead of being mislabeled.
- The Add/Edit Camera dialog had no way to select a storage target at
  all, even though the backend (`storage_target_id`) fully supported it --
  every camera silently used the default local storage with no way to
  point it at a registered USB/SSD/network target from the UI. Added a
  Storage target dropdown, populated from `/api/storage/targets`.
- Live view could intermittently go blank or a camera would flicker
  online/offline for no apparent reason, when the health-check probe
  reconnected on its own 15s timer while another connection (live view,
  an active recording, or the camera's own phone app) already held the
  camera's single available RTSP client slot -- true of most budget/
  consumer cameras, which don't support multiple simultaneous RTSP
  clients. The probe now skips entirely for any camera `RecordingEngine`
  already confirms is actively recording (conclusive proof of
  connectivity on its own), and the default probe interval was raised
  from 15s to 45s to further reduce contention frequency for cameras not
  currently recording.
- **Cameras showed OFFLINE even with correct, working credentials.** The
  background health-check probe (`CameraManager`) built its RTSP test URL
  without ever injecting the camera's username/password, so any camera
  requiring auth would always fail the probe and appear offline --
  regardless of whether live view, recording, or snapshots (which did
  authenticate correctly) worked fine. This credential-injection logic was
  duplicated slightly differently in three separate places (the probe,
  `RecordingEngine`, and two route handlers); consolidated into one
  shared `build_authenticated_rtsp_url()` helper in
  `app/cameras/url_utils.py` used everywhere, so this class of bug can't
  reoccur from the same URLs drifting out of sync.
- Editing a camera always cleared its username field even if unchanged,
  since the API never returned it and the edit dialog always started
  blank; saving without re-entering it would silently wipe the stored
  username. `username` is now included in the camera API response (not
  sensitive) and prefilled on edit; password remains intentionally never
  returned, with a placeholder clarifying blank = keep current password.
- ONVIF discovery results were only logged to the browser's developer
  console, invisible to a normal user. Added a proper results table
  (device name, hardware, source IP, service address) matching the
  network-scan results UI, with a "Use this IP" button to prefill the
  Add Camera dialog.
- ONVIF discovery crashed with `NotImplementedError` under production
  conditions: it used `loop.sock_sendto()`/`loop.sock_recvfrom()`, which
  the default asyncio event loop implements but `uvloop` (installed and
  used by default via `uvicorn[standard]`, i.e. what actually runs in
  production) does not. Rewrote discovery to use
  `loop.create_datagram_endpoint()` with a `DatagramProtocol`, which both
  event loop implementations support correctly.
- Two flaky/incorrect tests caught by CI: `test_session_token_expired` relied
  on a 1.1s sleep vs 1s max_age, which the signer's whole-second time
  resolution could round away depending on phase alignment; now sleeps
  1.2s against max_age=0, which is safe regardless of phase.
  `test_ffmpeg_command_uses_encode_when_overlay_enabled` hardcoded
  `Camera(id=1, ...)`, which could collide with another test's
  auto-assigned id; now uses the ORM-assigned `camera.id` instead.
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
