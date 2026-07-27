"""
Block-device discovery and formatting for the Storage page's "format a new
device" feature.

This is deliberately narrow and defensive: formatting is destructive and
irreversible, so this module refuses outright to touch anything on the
same physical disk the OS is running from (root filesystem or boot
partition), regardless of what path is requested. Every format request
also requires the caller to echo back the exact device path as a
confirmation string, so a single clicked button can never accidentally
trigger it -- this always requires an explicit, conscious repeat of the
target path (enforced both in the UI and again here server-side).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger("pi_nvr.storage.device_format")

# The only filesystem this feature offers. ext4 matches the project's own
# storage and has full Unix permission support, unlike exFAT/FAT32 --
# deliberately not offering a choice here keeps the safety story simple.
ALLOWED_FILESYSTEMS = {"ext4"}

DEVICE_PATH_RE = re.compile(r"^/dev/[a-zA-Z0-9]+$")


@dataclass
class BlockDevice:
    name: str
    path: str
    size_bytes: int
    fstype: str | None
    mountpoint: str | None
    type: str  # "disk" or "part"
    parent: str | None  # parent disk name, for partitions
    protected: bool = False
    protected_reason: str | None = None


class DeviceFormatError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _protected_disk_names() -> set[str]:
    """Returns the underlying disk name(s) (e.g. 'mmcblk0') backing the
    root filesystem and boot partition -- these, and anything on them,
    must never be formattable, no matter what the caller requests."""
    protected: set[str] = set()
    for mountpoint in ("/", "/boot/firmware"):
        try:
            result = _run(["findmnt", "-n", "-o", "SOURCE", mountpoint])
        except (subprocess.SubprocessError, OSError):
            continue
        source = result.stdout.strip()
        if not source:
            continue
        try:
            parent_result = _run(["lsblk", "-no", "PKNAME", source])
        except (subprocess.SubprocessError, OSError):
            parent_result = None
        parent = parent_result.stdout.strip() if parent_result else ""
        if parent:
            protected.add(parent)
        base_name = source.rsplit("/", 1)[-1]
        protected.add(re.sub(r"p?\d+$", "", base_name) or base_name)
    return protected


def list_devices() -> list[BlockDevice]:
    """Lists every block device and partition on the system, marking
    which ones are protected (part of the OS's own disk) so the UI can
    grey those out instead of offering them as format targets."""
    try:
        result = _run(["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,FSTYPE,MOUNTPOINT,TYPE,PKNAME"])
    except (subprocess.SubprocessError, OSError) as exc:
        raise DeviceFormatError(f"Could not list block devices: {exc}") from exc
    if result.returncode != 0:
        raise DeviceFormatError(f"lsblk failed: {result.stderr.strip()}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DeviceFormatError(f"Could not parse lsblk output: {exc}") from exc

    protected_names = _protected_disk_names()
    devices: list[BlockDevice] = []

    def _walk(entries, parent_name=None):
        for entry in entries:
            name = entry.get("name", "")
            device_type = entry.get("type", "")
            is_protected = name in protected_names or (parent_name and parent_name in protected_names)
            if device_type in ("part", "disk"):
                devices.append(BlockDevice(
                    name=name,
                    path=entry.get("path") or f"/dev/{name}",
                    size_bytes=int(entry.get("size") or 0),
                    fstype=entry.get("fstype"),
                    mountpoint=entry.get("mountpoint"),
                    type=device_type,
                    parent=parent_name,
                    protected=bool(is_protected),
                    protected_reason=(
                        "Part of the system's own OS disk -- refusing to offer this as a format target"
                        if is_protected else None
                    ),
                ))
            children = entry.get("children") or []
            if children:
                _walk(children, parent_name=name)

    _walk(data.get("blockdevices", []))
    return devices


def format_device(device_path: str, confirm: str, filesystem: str = "ext4") -> None:
    """Unmounts (if mounted) and formats the given device path. Requires
    `confirm` to exactly match `device_path` -- a deliberate friction
    point so this can never fire from anything but an explicit,
    conscious action, and re-validates that neither the device nor its
    parent disk is the one the OS is running from, regardless of what
    the UI already checked."""
    if not DEVICE_PATH_RE.match(device_path):
        raise DeviceFormatError(f"Not a valid device path: {device_path}")
    if confirm != device_path:
        raise DeviceFormatError("Confirmation text does not match the device path")
    if filesystem not in ALLOWED_FILESYSTEMS:
        raise DeviceFormatError(f"Unsupported filesystem: {filesystem}")

    protected_names = _protected_disk_names()
    device_name = device_path.rsplit("/", 1)[-1]
    try:
        parent_result = _run(["lsblk", "-no", "PKNAME", device_path])
        parent_name = parent_result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        parent_name = None

    if device_name in protected_names or (parent_name and parent_name in protected_names):
        raise DeviceFormatError(
            "Refusing to format this device: it is part of the system's own OS disk."
        )

    logger.warning("Formatting device %s as %s (user-confirmed)", device_path, filesystem)

    # The pi-nvr service runs as an unprivileged, non-root user with
    # NoNewPrivileges=true (see SECURITY.md) -- sudo cannot work here at
    # all, that hardening flag blocks it at the kernel level regardless
    # of sudoers configuration. Instead, this triggers a separate,
    # narrowly-scoped root-owned systemd template unit
    # (pi-nvr-format@.service) via `systemctl start --wait`, authorized
    # by a specific polkit rule that permits the pi-nvr user to start
    # *only* units matching that one template -- nothing else. The unit
    # itself re-validates the device isn't the OS's own disk before
    # doing anything, as defense in depth independent of this check.
    escape_result = _run(["systemd-escape", device_path], timeout=10)
    if escape_result.returncode != 0:
        raise DeviceFormatError(f"Could not escape device path: {escape_result.stderr.strip()}")
    instance_name = escape_result.stdout.strip()

    result = _run(
        ["systemctl", "start", "--wait", f"pi-nvr-format@{instance_name}.service"],
        timeout=300,
    )
    if result.returncode != 0:
        journal = _run(
            ["journalctl", "-u", f"pi-nvr-format@{instance_name}.service", "-n", "20", "--no-pager"],
            timeout=10,
        )
        raise DeviceFormatError(
            f"Format failed (see system logs for detail): {journal.stdout.strip()[-500:]}"
        )

    logger.info("Successfully formatted %s as %s", device_path, filesystem)
