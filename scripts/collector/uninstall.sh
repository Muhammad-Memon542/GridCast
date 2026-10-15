#!/usr/bin/env bash
# Remove the GridCast collector LaunchAgent. Collected data in data/ is left untouched.
set -euo pipefail

LABEL="local.gridcast.collector"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Removed $LABEL."
