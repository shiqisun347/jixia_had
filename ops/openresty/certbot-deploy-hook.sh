#!/usr/bin/env bash
set -euo pipefail

readonly SOURCE_DIR="/etc/letsencrypt/live/debate.vsagents.online"
readonly TARGET_DIR="/opt/1panel/apps/openresty/openresty/conf/ssl"
readonly CONTAINER="1Panel-openresty-7dwq"

install -d -o root -g root -m 0755 "$TARGET_DIR"
cp -L "$SOURCE_DIR/fullchain.pem" "$TARGET_DIR/fullchain.pem"
cp -L "$SOURCE_DIR/privkey.pem" "$TARGET_DIR/privkey.pem"
chown root:root "$TARGET_DIR/fullchain.pem" "$TARGET_DIR/privkey.pem"
chmod 0644 "$TARGET_DIR/fullchain.pem"
chmod 0600 "$TARGET_DIR/privkey.pem"

docker exec "$CONTAINER" openresty -t
docker kill --signal HUP "$CONTAINER" >/dev/null
