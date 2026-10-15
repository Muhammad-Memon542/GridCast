#!/usr/bin/env bash
# Idempotent setup of the GridCast production server (Ubuntu 24.04, ADR-002).
# Run as the admin user:  ssh gridcast@<ip> 'bash -s' < scripts/server/bootstrap.sh
set -euo pipefail

REPO_URL="https://github.com/Muhammad-Memon542/GridCast.git"
APP_DIR="$HOME/gridcast"

echo "== system packages"
sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    git sqlite3 python3-venv fail2ban unattended-upgrades >/dev/null
sudo systemctl enable --now fail2ban unattended-upgrades >/dev/null

echo "== 2 GB swap (the VM has 1 GB of RAM)"
if ! swapon --show | grep -q /swapfile; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== uv (from PyPI into a private venv)"
if [[ ! -x "$HOME/.uv-venv/bin/uv" ]]; then
    python3 -m venv "$HOME/.uv-venv"
    "$HOME/.uv-venv/bin/pip" install -q uv
fi
UV="$HOME/.uv-venv/bin/uv"

echo "== application code"
if [[ -d "$APP_DIR/.git" ]]; then
    git -C "$APP_DIR" pull -q --ff-only
else
    git clone -q "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
"$UV" python install -q 3.12
"$UV" sync -q --locked --no-dev
mkdir -p data
[[ -f .env ]] || printf 'GRIDCAST_ENV=prod\nGRIDCAST_CONTACT=%s\n' \
    "https://github.com/Muhammad-Memon542/GridCast" > .env
chmod 600 .env

echo "== hourly collector (systemd timer)"
sudo install -m 644 scripts/server/systemd/gridcast-collector.service /etc/systemd/system/
sudo install -m 644 scripts/server/systemd/gridcast-collector.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gridcast-collector.timer >/dev/null

echo "== done"
systemctl list-timers gridcast-collector.timer --no-pager | head -3
