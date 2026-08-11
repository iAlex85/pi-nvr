"""
On-demand conversion of recordings into a format browsers can actually
play inline.

Recordings are stored as MKV (see app/recording/engine.py's module
docstring) because this project's camera outputs PCM A-law audio, which
the MP4 muxer rejects outright when stream-copying. That was the right
call for *storage* -- but MKV has essentially no native playback support
in browsers (Safari has none at all, regardless of the codecs inside;
Chrome/Firefox support is inconsistent). A browser's <video> tag will
refuse to play an MKV file even though the video stream inside is
ordinary H.264 (this project's recordings always re-encode to H.264 when
the timestamp/name overlay is enabled, which it is by default -- overlay
requires decoding+encoding anyway, since drawtext can't be stream-copied).

So: convert once, on first playback request, and cache the result
alongside the original. Since the video is already H.264, this is a
cheap operation -- video is stream-copied (no re-encode), only the audio
needs transcoding (PCM A-law -> AAC, which MP4 accepts), so it runs much
faster than real-time even on a Pi 3. The original MKV is left untouched
for download/export (an unmodified copy of exactly what the camera sent
matters more there than it does for a disposable playback cache).
"""
from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger("pi_nvr.playback.transcode")

CONVERT_TIMEOUT_SECONDS = 60
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class TranscodeError(RuntimeError):
    pass


def _lock_for(path_str: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(path_str)
        if lock is None:
            lock = threading.Lock()
            _locks[path_str] = lock
        return lock


def cache_path_for(source_path: Path) -> Path:
    return source_path.parent / "playback_cache" / f"{source_path.stem}.mp4"


def ensure_playable(source_path: Path) -> Path:
    """Returns a path to a browser-playable version of `source_path`.
    If `source_path` is already a browser-friendly format (currently:
    anything not .mkv), returns it unchanged. For .mkv, returns a cached
    derived .mp4, converting (and caching) it first if this is the first
    time this recording has been requested for playback.

    Synchronous and blocking (a stream-copy + audio-only transcode of a
    short clip is fast enough to do inline within a request -- this
    project's recordings are typically well under a minute each given
    this camera's session behavior) -- FastAPI runs sync routes in a
    thread pool, so this doesn't block the event loop."""
    if source_path.suffix.lower() != ".mkv":
        return source_path

    dest = cache_path_for(source_path)
    if dest.exists() and dest.stat().st_mtime >= source_path.stat().st_mtime:
        return dest

    lock = _lock_for(str(source_path))
    with lock:
        # Re-check after acquiring the lock -- a concurrent request may
        # have already finished the conversion while this one waited.
        if dest.exists() and dest.stat().st_mtime >= source_path.stat().st_mtime:
            return dest

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_dest = dest.with_suffix(".mp4.tmp")

        cmd = [
            "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
            "-i", str(source_path),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            # ffmpeg normally picks the output muxer by guessing from the
            # file extension -- the .mp4.tmp temp filename (deliberately
            # non-final so a reader never sees a half-written file) isn'"'"'t
            # a recognized extension, so that guess fails outright
            # ("unable to choose an output format"). Being explicit here
            # means the temp filename'"'"'s exact shape stops mattering.
            "-f", "mp4",
            str(tmp_dest),
        ]
        logger.info("Converting %s for browser playback", source_path.name)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=CONVERT_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired as exc:
            tmp_dest.unlink(missing_ok=True)
            raise TranscodeError(f"Conversion timed out after {CONVERT_TIMEOUT_SECONDS}s") from exc

        if result.returncode != 0 or not tmp_dest.exists():
            tmp_dest.unlink(missing_ok=True)
            raise TranscodeError(f"ffmpeg failed: {result.stderr.strip()[-500:]}")

        tmp_dest.rename(dest)
        logger.info("Cached playable version at %s", dest)
        return dest


def delete_cached(source_path: Path) -> None:
    """Removes a cached derived .mp4 for `source_path`, if any -- called
    when the original recording is deleted, so the cache doesn't outlive
    the recording it was derived from."""
    dest = cache_path_for(source_path)
    dest.unlink(missing_ok=True)
