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

create_admin_account() {
  log "Create the initial admin account (no default password is shipped):"
  sudo -u "$SERVICE_USER" "$INSTALL_PREFIX/venv/bin/python3" "$INSTALL_PREFIX/scripts/create_admin.py"
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
  install_systemd_service
  create_admin_account
  start_service

  IP_ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
  PORT="$(grep -m1 'port:' "$CONFIG_DIR/config.yaml" | awk '{print $2}')"
  log "Installation complete."
  log "Open http://${IP_ADDR:-<this-device-ip>}:${PORT:-8080} to sign in."
  log "Manage the service with: systemctl [start|stop|restart|status] pi-nvr"
}

main "$@"
