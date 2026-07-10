"""System resource stats for the dashboard: CPU, RAM, disk, temperature,
uptime. Uses psutil for portability; temperature falls back gracefully on
non-Pi hardware where the thermal zone file doesn't exist."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import psutil

_BOOT_TIME = psutil.boot_time()

RPI_THERMAL_PATH = Path("/sys/class/thermal/thermal_zone0/temp")


def cpu_percent() -> float:
    return psutil.cpu_percent(interval=0.2)


def ram_usage() -> dict:
    mem = psutil.virtual_memory()
    return {
        "total_bytes": mem.total,
        "used_bytes": mem.used,
        "available_bytes": mem.available,
        "percent": mem.percent,
    }


def disk_usage(path: str = "/") -> dict:
    usage = shutil.disk_usage(path)
    return {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "percent": round((usage.used / usage.total) * 100, 1) if usage.total else 0.0,
    }


def cpu_temperature_celsius() -> float | None:
    if RPI_THERMAL_PATH.exists():
        try:
            raw = RPI_THERMAL_PATH.read_text().strip()
            return round(int(raw) / 1000, 1)
        except (OSError, ValueError):
            pass
    # Fallback for non-Pi Linux hosts that expose sensors via psutil.
    try:
        temps = psutil.sensors_temperatures()
        for entries in temps.values():
            if entries:
                return round(entries[0].current, 1)
    except (AttributeError, OSError):
        pass
    return None


def uptime_seconds() -> float:
    return time.time() - _BOOT_TIME


def full_snapshot(disk_path: str = "/") -> dict:
    return {
        "cpu_percent": cpu_percent(),
        "ram": ram_usage(),
        "disk": disk_usage(disk_path),
        "temperature_celsius": cpu_temperature_celsius(),
        "uptime_seconds": uptime_seconds(),
    }
