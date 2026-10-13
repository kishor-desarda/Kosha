#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Installs Kosha as a systemd service on Ubuntu 24.04 LTS (or any recent Debian/Ubuntu).
#   sudo deploy/linux/install.sh            install or upgrade from this checkout
# Safe to run again: data in /var/lib/kosha and the settings in /etc/kosha/kosha.env are never overwritten.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run as root: sudo $0"; exit 1; }
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
APP=/opt/kosha
DATA=/var/lib/kosha
ETC=/etc/kosha

apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip rsync >/dev/null
POSTGRES=""
if grep -qs '^KOSHA_DATABASE_URL=postgres' "$ETC/kosha.env"; then
  POSTGRES=1                                            # Kosha on PostgreSQL: pg_dump for the backups
  apt-get install -y -qq postgresql-client >/dev/null
fi

id kosha >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin kosha
install -d -o kosha -g kosha -m 0750 "$DATA"
install -d -m 0755 "$APP" "$ETC"

if [[ -d "$DATA/blobs" ]]; then
  echo "Existing data found: taking a backup before upgrading..."
  "$APP/venv/bin/python" -m kosha.backup "$DATA/backups/pre-upgrade-$(date +%Y%m%d-%H%M%S)" || {
    echo "Backup failed, so nothing was changed."; exit 1; }
fi

rsync -a --delete --exclude venv --exclude '.git' --exclude 'server/data' --exclude 'demo-data' "$SRC/" "$APP/"
[[ -d "$APP/venv" ]] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q -r "$APP/server/requirements.txt"
"$APP/venv/bin/pip" install -q "$APP/sdk/python"
[[ -z "$POSTGRES" ]] || "$APP/venv/bin/pip" install -q "psycopg[binary]>=3.1"

if [[ ! -f "$ETC/kosha.env" ]]; then
  sed "s#^KOSHA_DATA_DIR=.*#KOSHA_DATA_DIR=$DATA#" "$APP/server/kosha.env.example" > "$ETC/kosha.env"
  PW="$(head -c 18 /dev/urandom | base64 | tr -d '/+=')"
  sed -i "s#^KOSHA_ADMIN_PASSWORD=.*#KOSHA_ADMIN_PASSWORD=$PW#" "$ETC/kosha.env"
  chmod 0640 "$ETC/kosha.env"; chgrp kosha "$ETC/kosha.env"
  echo "First admin password: $PW  (also in $ETC/kosha.env; change it after signing in)"
fi
ln -sf "$ETC/kosha.env" "$APP/server/kosha.env"

install -m 0644 "$APP/deploy/linux/kosha.service" /etc/systemd/system/kosha.service
install -m 0644 "$APP/deploy/linux/kosha-backup.service" /etc/systemd/system/kosha-backup.service
install -m 0644 "$APP/deploy/linux/kosha-backup.timer" /etc/systemd/system/kosha-backup.timer
install -m 0644 "$APP/deploy/linux/kosha-restore-test.service" /etc/systemd/system/kosha-restore-test.service
install -m 0644 "$APP/deploy/linux/kosha-restore-test.timer" /etc/systemd/system/kosha-restore-test.timer
systemctl daemon-reload
systemctl enable --now kosha.service kosha-backup.timer kosha-restore-test.timer
systemctl restart kosha.service
echo "Kosha is running on 127.0.0.1:8080. Put an HTTPS front end in front of it (see deploy/linux/Caddyfile.example)."
