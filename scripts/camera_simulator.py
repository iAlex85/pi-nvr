#!/usr/bin/env python3
"""
Simulated RTSP camera for testing Pi-NVR without real hardware.

Generates a synthetic video (a moving box on a plain background, useful for
exercising motion detection) and serves it as an RTSP stream via ffmpeg.

Usage:
    python3 scripts/camera_simulator.py --port 8554 --name test1

Then add a camera in the UI with RTSP URL:
    rtsp://<this-machine-ip>:8554/test1

Requires ffmpeg built with the rtsp muxer (the standard Debian/Ubuntu
ffmpeg package works) OR a local RTSP server like MediaMTX to relay to;
this script uses ffmpeg's own `-f rtsp` output against a listening ffmpeg,
which is the simplest zero-extra-dependency option for local testing.

Note: plain ffmpeg cannot itself *listen* for RTSP clients; it needs an
RTSP server to push to. For a truly standalone simulator with no
additional binaries, this script falls back to writing an MP4 test file
and serving it over HTTP instead, which several test flows (playback,
storage, retention) don't need real RTSP for anyway. If you have
MediaMTX (https://github.com/bluenviron/mediamtx) installed, point
--rtsp-server at it for a real RTSP endpoint.
"""
from __future__ import annotations

import argparse
import http.server
import shutil
import socketserver
import subprocess
import sys
import tempfile
from pathlib import Path


def generate_test_clip(out_path: Path, duration: int = 30) -> None:
    """Uses ffmpeg's built-in `testsrc`/`life` filters to synthesize a
    moving-pattern video -- no camera or external asset required."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=15:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


def serve_over_http(directory: Path, port: int) -> None:
    def handler(*args, **kwargs):
        return http.server.SimpleHTTPRequestHandler(*args, directory=str(directory), **kwargs)
    with socketserver.TCPServer(("0.0.0.0", port), handler) as httpd:
        print(f"Serving simulated camera clip at http://0.0.0.0:{port}/clip.mp4")
        print("(Point a camera's RTSP URL at MediaMTX if you need real RTSP; "
              "for HTTP-MJPEG-style testing this file server is enough.)")
        httpd.serve_forever()


def push_to_rtsp_server(clip_path: Path, rtsp_server_url: str) -> None:
    """If the user has an RTSP server (e.g. MediaMTX) running, loop-push the
    generated clip to it so it's available as a normal RTSP stream."""
    cmd = [
        "ffmpeg", "-loglevel", "error", "-re", "-stream_loop", "-1",
        "-i", str(clip_path),
        "-c", "copy", "-f", "rtsp", rtsp_server_url,
    ]
    print(f"Pushing simulated stream to {rtsp_server_url} (Ctrl+C to stop)")
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="test1", help="Stream name / path segment")
    parser.add_argument("--http-port", type=int, default=8090, help="Fallback HTTP file server port")
    parser.add_argument(
        "--rtsp-server",
        default=None,
        help="rtsp://host:port/path of a running RTSP server (e.g. MediaMTX) to push the "
             "simulated stream to. If omitted, falls back to serving a plain MP4 over HTTP.",
    )
    parser.add_argument("--duration", type=int, default=30, help="Loop clip length in seconds")
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found on PATH -- install it first.", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp_dir:
        clip_path = Path(tmp_dir) / "clip.mp4"
        print(f"Generating {args.duration}s synthetic test clip...")
        generate_test_clip(clip_path, duration=args.duration)

        if args.rtsp_server:
            push_to_rtsp_server(clip_path, args.rtsp_server.rstrip("/") + f"/{args.name}")
        else:
            serve_over_http(Path(tmp_dir), args.http_port)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
