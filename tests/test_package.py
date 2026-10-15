from __future__ import annotations

import importlib
import pkgutil
import socket
import tomllib

import pytest
from pytest_socket import SocketBlockedError

import gridcast
from gridcast.config import REPO_ROOT


def test_version_matches_pyproject() -> None:
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        assert gridcast.__version__ == tomllib.load(fh)["project"]["version"]


def test_all_subpackages_import() -> None:
    names = [m.name for m in pkgutil.walk_packages(gridcast.__path__, prefix="gridcast.")]
    assert "gridcast.ingest" in names
    for name in names:
        importlib.import_module(name)


@pytest.mark.filterwarnings("ignore:A test tried to use socket")
def test_network_is_blocked_in_tests() -> None:
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
