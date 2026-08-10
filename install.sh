#!/usr/bin/env bash
#
# Pi-NVR installer.
#
# Installs system dependencies, creates a Python virtualenv, sets up
# directories, generates systemd secrets, installs the systemd service,
# and starts it. No Docker, no Node.js -- everything runs directly on the
# host, which matters on a Pi 3's 1 GB of RAM.
#
# Usage:
#   git clone <repo> pi-nvr && cd pi-nvr
#   ./install.sh
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_PREFIX="/opt/pi-nvr"
CONFIG_DIR="/etc/pi-nvr"
ENV_FILE="$CONFIG_DIR/environment"
SERVICE_USER="pi-nvr"
SYSTEMD_UNIT="/etc/systemd/system/pi-nvr.service"

log()  { echo -e "\033[1;33m[pi-nvr]\033[0m $*"; }
err()  { echo -e "\033[1;31m[pi-nvr]\033[0m $*" >&2; }

require_root() {
  if [[ $EUID -ne 0 ]]; then
    err "install.sh must be run as root (sudo ./install.sh)"
    exit 1
  fi
}

detect_platform() {
  if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    log "Detected: $PRETTY_NAME"
  fi
  ARCH="$(uname -m)"
  log "Architecture: $ARCH"
}

install_system_dependencies() {
  log "Installing system packages (ffmpeg, python3-venv, sqlite3)..."
  apt-get update -qq
  apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    ffmpeg \
    sqlite3 \
    git curl ca-certificates
}

create_service_user() {
  if ! id "$SERVICE_USER" &>/dev/null; then
    log "Creating service user '$SERVICE_USER'..."
    useradd --system --home "$INSTALL_PREFIX" --shell /usr/sbin/nologin "$SERVICE_USER"
    # Video group membership is often needed for local capture devices;
    # harmless if unused for pure-RTSP setups.
    usermod -aG video "$SERVICE_USER" || true
  fi
}

copy_application() {
  log "Installing application to $INSTALL_PREFIX..."
  mkdir -p "$INSTALL_PREFIX"
  rsync -a --exclude ".git" --exclude "recordings" --exclude "database" --exclude "logs" \
    "$REPO_DIR"/ "$INSTALL_PREFIX"/ 2>/dev/null || \
    cp -r "$REPO_DIR"/. "$INSTALL_PREFIX"/

  mkdir -p "$INSTALL_PREFIX"/{recordings,database,logs,config/schedules}
}

create_virtualenv() {
  log "Creating Python virtual environment..."
  python3 -m venv "$INSTALL_PREFIX/venv"
  "$INSTALL_PREFIX/venv/bin/pip" install --upgrade pip wheel
  "$INSTALL_PREFIX/venv/bin/pip" install -r "$INSTALL_PREFIX/requirements.txt"
}

write_config() {
  mkdir -p "$CONFIG_DIR"
  if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
    log "Writing default configuration to $CONFIG_DIR/config.yaml"
    cp "$INSTALL_PREFIX/config/default_config.yaml" "$CONFIG_DIR/config.yaml"
    # Point the installed config at the real data directories, not the
    # relative dev-checkout paths in default_config.yaml.
    sed -i "s#path: \"database/pi-nvr.db\"#path: \"$INSTALL_PREFIX/database/pi-nvr.db\"#" "$CONFIG_DIR/config.yaml"
    sed -i "s#path: \"recordings\"#path: \"$INSTALL_PREFIX/recordings\"#" "$CONFIG_DIR/config.yaml"
    sed -i "s#dir: \"logs\"#dir: \"$INSTALL_PREFIX/logs\"#" "$CONFIG_DIR/config.yaml"
  else
    log "Existing config found at $CONFIG_DIR/config.yaml -- leaving it untouched."
  fi
}

write_secrets() {
  if [[ ! -f "$ENV_FILE" ]]; then
    log "Generating session/DB encryption secrets..."
    SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
    DB_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
    cat > "$ENV_FILE" <<EOF
PI_NVR_CONFIG=$CONFIG_DIR/config.yaml
PI_NVR_SESSION_SECRET=$SESSION_SECRET
PI_NVR_DB_SECRET=$DB_SECRET
EOF
    chmod 600 "$ENV_FILE"
  else
    log "Existing secrets file found -- leaving it untouched."
  fi
}

set_permissions() {
  chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_PREFIX"
  chown -R "$SERVICE_USER":"$SERVICE_USER" "$CONFIG_DIR"
}

install_format_helper() {
  # The Storage page's "format a new device" feature needs to run
  # `umount`/`mkfs.ext4` as root, but pi-nvr.service runs as an
  # unprivileged user with NoNewPrivileges=true (see SECURITY.md) --
  # that flag blocks sudo/setuid at the kernel level, so a sudoers rule
  # would silently not work here. Instead: a separate, root-owned
  # systemd template unit (pi-nvr-format@.service) runs the actual
  # formatting, triggered by the unprivileged service via `systemctl
  # start --wait` (a D-Bus call to systemd's already-privileged PID 1,
  # not a privilege escalation of pi-nvr's own process, so
  # NoNewPrivileges doesn't block it). A polkit rule authorizes the
  # pi-nvr user to start *only* units matching that one template name.
  log "Installing device-format helper (systemd unit + polkit rule)..."

  if ! command -v pkaction >/dev/null 2>&1 && ! command -v polkitd >/dev/null 2>&1; then
    apt-get install -y polkitd 2>/dev/null || apt-get install -y policykit-1 2>/dev/null || {
      err "Could not install polkit -- device formatting from the UI will not work. Manual mkfs.ext4/umount from the shell is unaffected."
      return 0
    }
  fi

  install -m 700 -o root -g root "$REPO_DIR/scripts/format_device.sh" "$INSTALL_PREFIX/scripts/format_device.sh"

  sed "s#__INSTALL_PREFIX__#$INSTALL_PREFIX#g" \
    "$REPO_DIR/systemd/pi-nvr-format@.service" > /etc/systemd/system/pi-nvr-format@.service

  mkdir -p /etc/polkit-1/rules.d
  sed "s#__SERVICE_USER__#$SERVICE_USER#g" \
    "$REPO_DIR/systemd/60-pi-nvr-format.rules" > /etc/polkit-1/rules.d/60-pi-nvr-format.rules
  chmod 644 /etc/polkit-1/rules.d/60-pi-nvr-format.rules

  systemctl daemon-reload
}

install_mount_helper() {
  # The Storage page'"'"'s "detected drives" auto-setup feature needs to run
  # `mkdir`/`mount -a`/append to /etc/fstab as root, same NoNewPrivileges
  # constraint as the format helper above -- see install_format_helper'"'"'s
  # comment for the full rationale, this mirrors it exactly for a
  # separate, narrower helper (pi-nvr-mount@.service) that mounts an
  # already-formatted drive and persists it to fstab instead of
  # formatting anything.
  log "Installing drive auto-setup helper (systemd unit + polkit rule)..."

  if ! command -v pkaction >/dev/null 2>&1 && ! command -v polkitd >/dev/null 2>&1; then
    apt-get install -y polkitd 2>/dev/null || apt-get install -y policykit-1 2>/dev/null || {
      err "Could not install polkit -- drive auto-setup from the UI will not work. Manual /etc/fstab editing from the shell is unaffected."
      return 0
    }
  fi

  install -m 700 -o root -g root "$REPO_DIR/scripts/mount_helper.sh" "$INSTALL_PREFIX/scripts/mount_helper.sh"

  sed "s#__INSTALL_PREFIX__#$INSTALL_PREFIX#g"     "$REPO_DIR/systemd/pi-nvr-mount@.service" > /etc/systemd/system/pi-nvr-mount@.service

  mkdir -p /etc/polkit-1/rules.d
  sed "s#__SERVICE_USER__#$SERVICE_USER#g"     "$REPO_DIR/systemd/61-pi-nvr-mount.rules" > /etc/polkit-1/rules.d/61-pi-nvr-mount.rules
  chmod 644 /etc/polkit-1/rules.d/61-pi-nvr-mount.rules

  systemctl daemon-reload
}

install_systemd_service() {
  log "Installing systemd service..."
  sed \
    -e "s#__INSTALL_PREFIX__#$INSTALL_PREFIX#g" \
    -e "s#__SERVICE_USER__#$SERVICE_USER#g" \
    -e "s#__ENV_FILE__#$ENV_FILE#g" \
    "$REPO_DIR/systemd/pi-nvr.service" > "$SYSTEMD_UNIT"

  systemctl daemon-reload
  systemctl enable pi-nvr.service
}

start_service() {
  log "Starting pi-nvr service..."
  systemctl start pi-nvr.service
  sleep 2
  systemctl status pi-nvr.service --no-pager || true
}

main() {
  require_root
  detect_platform
  install_system_dependencies
  create_service_user
  copy_application
  create_virtualenv
  write_config
  write_secrets
  set_permissions
  install_format_helper
  install_mount_helper
  install_systemd_service
  start_service

  IP_ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
  PORT="$(grep -m1 'port:' "$CONFIG_DIR/config.yaml" | awk '{print $2}')"
  log "Installation complete."
  log "Open http://${IP_ADDR:-<this-device-ip>}:${PORT:-8080} to create your admin account and sign in."
  log "Manage the service with: systemctl [start|stop|restart|status] pi-nvr"
}

main "$@"
