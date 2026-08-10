#!/usr/bin/env bash
#
# Mounts an already-formatted drive and persists it to /etc/fstab. This
# script runs as root, but is only ever invoked via the pi-nvr-mount@.service
# systemd template unit -- the unprivileged pi-nvr service triggers that
# unit via `systemctl start --wait`, authorized by a narrow polkit rule
# that permits starting *only* units matching that one template name.
# This script itself re-validates the device isn't the OS's own disk
# before doing anything, as defense in depth independent of the
# Python-level check that already happened before systemctl was even
# called (see app/storage/device_format.py:setup_detected_drive).
#
# Idempotent: safe to re-run on a drive that's already partly or fully
# set up (already mounted, already in fstab, or both) -- it just skips
# whatever's already done rather than erroring or duplicating entries.
#
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <systemd-escaped-device-path>" >&2
  exit 1
fi

DEVICE_PATH="$(systemd-escape --unescape "$1")"

if [[ ! "$DEVICE_PATH" =~ ^/dev/[a-zA-Z0-9]+$ ]]; then
  echo "Refusing: not a plausible device path: $DEVICE_PATH" >&2
  exit 1
fi

DEVICE_NAME="$(basename "$DEVICE_PATH")"

ROOT_SOURCE="$(findmnt -n -o SOURCE / || true)"
ROOT_DISK=""
if [[ -n "$ROOT_SOURCE" ]]; then
  ROOT_DISK="$(lsblk -no PKNAME "$ROOT_SOURCE" 2>/dev/null || true)"
fi

BOOT_SOURCE="$(findmnt -n -o SOURCE /boot/firmware 2>/dev/null || true)"
BOOT_DISK=""
if [[ -n "$BOOT_SOURCE" ]]; then
  BOOT_DISK="$(lsblk -no PKNAME "$BOOT_SOURCE" 2>/dev/null || true)"
fi

for protected in "$ROOT_DISK" "$BOOT_DISK"; do
  if [[ -n "$protected" && "$DEVICE_NAME" == "$protected"* ]]; then
    echo "Refusing: $DEVICE_PATH is part of the OS's own disk ($protected)" >&2
    exit 1
  fi
done

UUID="$(blkid -s UUID -o value "$DEVICE_PATH" || true)"
if [[ -z "$UUID" ]]; then
  echo "Refusing: $DEVICE_PATH has no filesystem UUID -- it needs formatting first (Storage page > Format a new device)" >&2
  exit 1
fi

FSTYPE="$(blkid -s TYPE -o value "$DEVICE_PATH" || true)"
if [[ -z "$FSTYPE" ]]; then
  echo "Refusing: could not determine filesystem type for $DEVICE_PATH" >&2
  exit 1
fi

LABEL="$(blkid -s LABEL -o value "$DEVICE_PATH" || true)"
# Sanitize the label for use in a path (drive labels can contain spaces,
# slashes, or nothing at all) -- fall back to a UUID-derived name if
# there's no usable label.
SAFE_LABEL="$(echo "${LABEL:-}" | tr -c 'A-Za-z0-9_-' '_' | sed 's/^_*//;s/_*$//')"
if [[ -z "$SAFE_LABEL" ]]; then
  SAFE_LABEL="drive"
fi
MOUNTPOINT="/media/pi-nvr-${SAFE_LABEL}-${UUID:0:8}"

echo "Device: $DEVICE_PATH  UUID: $UUID  Filesystem: $FSTYPE"
echo "Target mountpoint: $MOUNTPOINT"

mkdir -p "$MOUNTPOINT"

if grep -q "UUID=$UUID " /etc/fstab 2>/dev/null; then
  echo "Already present in /etc/fstab, leaving it as-is."
else
  # nofail: matches this project's existing convention (see the
  # manually-added SanDisk entry this feature is meant to replace the
  # need for) -- a missing/unplugged drive must never hang boot.
  echo "UUID=$UUID  $MOUNTPOINT  $FSTYPE  defaults,nofail  0  2" >> /etc/fstab
  echo "Added /etc/fstab entry."
fi

echo "Running 'mount -a' to apply (does not affect already-mounted filesystems)..."
mount -a

echo "Done. Mounted at: $MOUNTPOINT"
