from __future__ import annotations

import os

import pytest


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Remove GridCast-related environment variables so tests see defaults only."""
    for name in list(os.environ):
        if name.startswith("GRIDCAST_") or name == "GRIDSTATUS_API_KEY":
            monkeypatch.delenv(name)
    return monkeypatch
