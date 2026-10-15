#!/usr/bin/env bash
# Install the GridCast live collector as a macOS LaunchAgent.
# It runs hourly and does nothing unless the Mac is on AC power (`--require-ac`).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
LABEL="local.gridcast.collector"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

if [[ ! -x "$REPO/.venv/bin/gridcast" ]]; then
    echo "error: $REPO/.venv/bin/gridcast not found; run 'make setup' first" >&2
    exit 1
fi

mkdir -p "$REPO/data/logs" "$HOME/Library/LaunchAgents"
sed "s#__REPO__#$REPO#g" "$REPO/scripts/collector/$LABEL.plist" > "$PLIST"
plutil -lint "$PLIST" >/dev/null

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installed $LABEL (hourly at :07, AC power only)."
echo "Logs:       $REPO/data/logs/collector.log"
echo "Gap report: make gaps"
