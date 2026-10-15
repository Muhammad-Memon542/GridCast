"""Power-source check so the collector can run only while the Mac is plugged in."""

from __future__ import annotations

import subprocess
import sys


def parse_pmset(output: str) -> bool:
    """``True`` if ``pmset -g batt`` reports AC power."""
    first_line = output.strip().splitlines()[0] if output.strip() else ""
    return "'AC Power'" in first_line


def on_ac_power() -> bool:
    """AC power on macOS; other platforms (CI, servers) are treated as mains-powered.

    If ``pmset`` cannot be run on a Mac we report battery, so the collector errs
    towards not running rather than draining the battery.
    """
    if sys.platform != "darwin":
        return True
    try:
        result = subprocess.run(
            ["/usr/bin/pmset", "-g", "batt"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return parse_pmset(result.stdout)
