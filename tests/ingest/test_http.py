from __future__ import annotations

import httpx
import pytest
import respx

from gridcast.ingest.http import FetchError, PoliteClient

URL = "https://reports.example.test/public/X/"


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _client(clock: FakeClock, max_retries: int = 4) -> PoliteClient:
    return PoliteClient("GridCast-test/0", sleep=clock.sleep, clock=clock, max_retries=max_retries)


@respx.mock
def test_identifies_itself_and_spaces_requests() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, text="ok"))
    clock = FakeClock()
    with _client(clock) as client:
        client.get(URL)
        clock.now += 0.25  # caller spends 0.25 s before the next request
        client.get(URL)
    assert route.calls[0].request.headers["User-Agent"] == "GridCast-test/0"
    assert clock.sleeps == [pytest.approx(0.75)]
    assert client.requests_made == 2


@respx.mock
def test_retries_connection_reset_with_backoff() -> None:
    respx.get(URL).mock(
        side_effect=[httpx.ConnectError("Connection reset by peer"), httpx.Response(200)]
    )
    clock = FakeClock()
    with _client(clock) as client:
        assert client.get(URL).status_code == 200
    # backoff 2 s, after which the 1 s rate limit is already satisfied
    assert clock.sleeps == [2.0]


@respx.mock
def test_honours_numeric_retry_after() -> None:
    respx.get(URL).mock(
        side_effect=[httpx.Response(503, headers={"Retry-After": "30"}), httpx.Response(200)]
    )
    clock = FakeClock()
    with _client(clock) as client:
        assert client.get(URL).status_code == 200
    assert clock.sleeps == [30.0]


@respx.mock
def test_gives_up_after_max_retries() -> None:
    respx.get(URL).mock(return_value=httpx.Response(500, headers={"Retry-After": "soon"}))
    clock = FakeClock()
    with _client(clock, max_retries=2) as client, pytest.raises(FetchError, match="3 attempts"):
        client.get(URL)
    assert clock.sleeps == [2.0, 4.0]


@respx.mock
def test_client_errors_are_returned_not_retried() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    clock = FakeClock()
    with _client(clock) as client:
        assert client.get(URL).status_code == 404
    assert route.call_count == 1
