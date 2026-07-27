#!/usr/bin/env bash
#
# Formats a block device as ext4. This script runs as root, but is only
# ever invoked via the pi-nvr-format@.service systemd template unit --
# the unprivileged pi-nvr service triggers that unit via `systemctl start
# --wait`, authorized by a narrow polkit rule that permits starting
# *only* units matching that one template name. This script itself
# re-validates the device isn't the OS's own disk before doing anything,
# as defense in depth independent of the Python-level check that already
# happened before systemctl was even called.
#
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <systemd-escaped-device-path>" >&2
  exit 1
fi

# systemd template units pass the instance name (the part after @) as
# the sole argument, already escaped by systemd-escape on the calling
# side (e.g. /dev/sdb1 arrives as "dev-sdb1"). Un-escape it back to a
# real path.
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

echo "Unmounting $DEVICE_PATH (if mounted)..."
umount "$DEVICE_PATH" 2>/dev/null || true

echo "Formatting $DEVICE_PATH as ext4..."
mkfs.ext4 -F "$DEVICE_PATH"

echo "Done."
