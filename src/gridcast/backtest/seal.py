"""Test seal (ADR-011): no test-window day is scored until configs are frozen and committed."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from gridcast.config import REPO_ROOT


class SealedWindowError(RuntimeError):
    """Raised when code tries to score a sealed (test-window) day."""


def _committed_and_clean(path: Path) -> bool:
    rel = path.resolve().relative_to(REPO_ROOT)
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(rel)], cwd=REPO_ROOT, capture_output=True
    )
    if tracked.returncode != 0:
        return False
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", str(rel)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return dirty.returncode == 0 and dirty.stdout.strip() == ""


@dataclass(frozen=True)
class WindowSeal:
    test_start: date
    unsealed: bool = False

    @classmethod
    def open_with(cls, test_start: date, frozen_config: Path) -> WindowSeal:
        """Unseal only for a frozen config file that is committed with no local changes."""
        if not frozen_config.exists() or not _committed_and_clean(frozen_config):
            raise SealedWindowError(
                f"{frozen_config} must exist, be committed and be unchanged before the test "
                "window can be scored (ADR-011)"
            )
        return cls(test_start=test_start, unsealed=True)

    def check(self, day: date) -> None:
        if day >= self.test_start and not self.unsealed:
            raise SealedWindowError(
                f"{day} is in the sealed test window (from {self.test_start}); "
                "freeze and commit the model config, then unseal (ADR-011)"
            )
