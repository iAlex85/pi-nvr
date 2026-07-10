# Changelog

All notable changes to this project are documented in this file.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

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
