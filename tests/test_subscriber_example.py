"""Tests for `_iter_with_connect_retry` in `examples/subscriber_example.py`.

The wrapper is meant to retry *only* the very first connection attempt (to survive the
publisher-not-listening-yet race documented in the README), not to retry, mask, or
reconnect on any failure that happens once iteration is actually under way. Exercised here
against a scripted fake iterator so the real network stack never has to be involved.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
from typing import TYPE_CHECKING

import pytest

from h2r import subscriber as subscriber_module
from tests import test_subscriber

if TYPE_CHECKING:
    import types
    from collections.abc import Iterable

    import h2.connection

_ASYNCIO_WAIT_FOR_TIMEOUT_SECONDS = 2

_EXAMPLE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "examples" / "subscriber_example.py"
)


def _load_subscriber_example() -> types.ModuleType:
    """Import `examples/subscriber_example.py` as a module (it isn't an installed package)."""
    spec = importlib.util.spec_from_file_location("subscriber_example", _EXAMPLE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


subscriber_example = _load_subscriber_example()


class _ScriptedSubscriber:
    """A fake `Subscriber`: yields *script* items in order, raising any exception entries."""

    def __init__(self, script: Iterable[bytes | BaseException]) -> None:
        self._script = list(script)
        self.attempts = 0

    def __aiter__(self) -> _ScriptedSubscriber:
        return self

    async def __anext__(self) -> bytes:
        self.attempts += 1
        if not self._script:
            raise StopAsyncIteration
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


async def test_retries_connection_refused_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subscriber_example, "_CONNECT_RETRY_DELAY_SECONDS", 0)
    fake = _ScriptedSubscriber(
        [ConnectionRefusedError(), ConnectionRefusedError(), b"first", b"second"],
    )

    received = [payload async for payload in subscriber_example._iter_with_connect_retry(fake)]

    assert received == [b"first", b"second"]
    # 2 refused attempts, then "first" (attempt 3), then "second" (attempt 4), then the
    # trailing `anext()` that finds the script empty and raises `StopAsyncIteration`
    # (attempt 5) to end the `async for` cleanly.
    assert fake.attempts == 5


async def test_gives_up_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subscriber_example, "_CONNECT_RETRY_DELAY_SECONDS", 0)
    attempts = subscriber_example._CONNECT_RETRY_ATTEMPTS
    fake = _ScriptedSubscriber([ConnectionRefusedError() for _ in range(attempts + 1)])

    with pytest.raises(ConnectionRefusedError):
        async for _ in subscriber_example._iter_with_connect_retry(fake):
            pass

    # Must stop retrying at exactly the documented attempt count, not one more or one fewer.
    assert fake.attempts == attempts


async def test_stops_cleanly_when_stream_ends_before_any_payload() -> None:
    fake = _ScriptedSubscriber([])

    async def collect() -> list[bytes]:
        return [payload async for payload in subscriber_example._iter_with_connect_retry(fake)]

    # If a `StopAsyncIteration` on the first `anext()` were mistaken for a retryable
    # failure (e.g. `continue` instead of `return`), this would hang forever rather than
    # fail an assertion; bound it so that regression shows up as a red test, not a stall.
    received = await asyncio.wait_for(collect(), timeout=_ASYNCIO_WAIT_FOR_TIMEOUT_SECONDS)

    assert received == []
    assert fake.attempts == 1


async def test_does_not_retry_a_non_refused_connection_error() -> None:
    fake = _ScriptedSubscriber([ConnectionError("publisher rejected subscription")])

    with pytest.raises(ConnectionError, match="publisher rejected subscription"):
        async for _ in subscriber_example._iter_with_connect_retry(fake):
            pass

    # A plain ConnectionError (e.g. the non-200-status case) is not a "still starting up"
    # signal: it must propagate on the first attempt, not get treated as retryable.
    assert fake.attempts == 1


async def test_does_not_retry_connection_refused_after_stream_already_started() -> None:
    fake = _ScriptedSubscriber([b"first", ConnectionRefusedError()])
    iterator = subscriber_example._iter_with_connect_retry(fake)

    first = await anext(iterator)

    # The retry loop only covers the very first `anext()`; a `ConnectionRefusedError` after
    # the stream is already flowing must not be silently retried or swallowed.
    with pytest.raises(ConnectionRefusedError):
        await anext(iterator)

    assert first == b"first"
    assert fake.attempts == 2


async def test_real_subscriber_reconnects_across_a_refused_first_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for the load-bearing assumption behind the whole retry wrapper.

    `_iter_with_connect_retry` only works because `Subscriber.__anext__` leaves the
    subscriber unstarted (so the *same* `anext()` call reconnects from scratch) when
    `_connect()` raises. `_ScriptedSubscriber` above can't catch a regression of that
    invariant (it's re-enterable by construction); this test drives the real
    `subscriber.Subscriber` and a real socket instead, with only the very first
    `asyncio.open_connection` calls forced to fail.
    """
    monkeypatch.setattr(subscriber_example, "_CONNECT_RETRY_DELAY_SECONDS", 0)
    payloads = [b"hello"]
    refusals_remaining = 2
    real_open_connection = asyncio.open_connection

    async def flaky_open_connection(
        host: str,
        port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        nonlocal refusals_remaining
        if refusals_remaining > 0:
            refusals_remaining -= 1
            raise ConnectionRefusedError
        return await real_open_connection(host, port)

    async def handler(
        connection: h2.connection.H2Connection,
        writer: asyncio.StreamWriter,
        stream_id: int,
        path: str,
    ) -> None:
        del path
        await test_subscriber._stream_payloads(connection, writer, stream_id, payloads)

    async with test_subscriber._FakeH2Server(handler) as address:
        monkeypatch.setattr(subscriber_module.asyncio, "open_connection", flaky_open_connection)

        async with subscriber_module.Subscriber(address, "/topic") as topic_subscriber:
            received = [
                payload
                async for payload in subscriber_example._iter_with_connect_retry(
                    topic_subscriber,
                )
            ]

    assert received == payloads
    # Both forced refusals were actually consumed by the retry loop, not skipped.
    assert refusals_remaining == 0
