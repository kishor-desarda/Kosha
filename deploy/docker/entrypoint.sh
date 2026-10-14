#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Start Kosha in the container as the unprivileged "kosha" user.
# Hosting platforms often mount the data volume owned by root; started as root, this hands the data folder to
# "kosha" first (nothing is deleted or moved), then drops root. Started as "kosha" already, it just runs.
set -e
DATA="${KOSHA_DATA_DIR:-/data}"
if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA"
    if [ "$(stat -c %U "$DATA")" != "kosha" ]; then
        chown -R kosha "$DATA"
    fi
    exec setpriv --reuid=kosha --regid="$(id -g kosha)" --init-groups "$@"
fi
exec "$@"
