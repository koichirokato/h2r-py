"""Subscriber tests against a real minimal HTTP/2 server (no mocks for the wire protocol).

The server below is a from-scratch, deliberately minimal ``h2``-based responder used only to
exercise :class:`h2r.subscriber.Subscriber` over an actual TCP socket. It is not
:mod:`h2r.publisher` (owned by a parallel branch) — just enough HTTP/2 to prove the
subscriber's framing and flow-control handling work against real bytes on the wire.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable
from collections.abc import Callable

import h2.config
import h2.connection
import h2.events
import pytest

from h2r import frame
from h2r import subscriber

_Handler = Callable[[h2.connection.H2Connection, asyncio.StreamWriter, int, str], Awaitable[None]]


class _FakeH2Server:
    """Accepts HTTP/2 connections and delegates each request to a test-supplied handler."""

    def __init__(self, handler: _Handler) -> None:
        self._handler = handler
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def __aenter__(self) -> str:
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        return f"127.0.0.1:{port}"

    async def __aexit__(self, *exc_info: object) -> None:
        del exc_info
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.ensure_future(self._handle_connection(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        connection = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=False))
        connection.initiate_connection()
        writer.write(connection.data_to_send())
        await writer.drain()
        try:
            stream_id, path = await _read_request(reader, connection, writer)
            if stream_id is not None and path is not None:
                await self._handler(connection, writer, stream_id, path)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            with contextlib.suppress(OSError):
                writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()


async def _read_request(
    reader: asyncio.StreamReader,
    connection: h2.connection.H2Connection,
    writer: asyncio.StreamWriter,
) -> tuple[int | None, str | None]:
    """Read until the request headers arrive, returning ``(stream_id, path)`` (or ``None``s)."""
    while True:
        data = await reader.read(65536)
        if not data:
            return None, None
        request_stream_id: int | None = None
        request_path: str | None = None
        for event in connection.receive_data(data):
            if isinstance(event, h2.events.RequestReceived):
                request_stream_id = event.stream_id
                path_value = next(value for name, value in event.headers if name == b":path")
                request_path = path_value.decode()
        outgoing = connection.data_to_send()
        if outgoing:
            writer.write(outgoing)
            await writer.drain()
        if request_stream_id is not None:
            return request_stream_id, request_path


async def _stream_payloads(
    connection: h2.connection.H2Connection,
    writer: asyncio.StreamWriter,
    stream_id: int,
    payloads: list[bytes],
) -> None:
    """Respond 200, then stream each payload as a frame split across two writes, then end."""
    connection.send_headers(stream_id, [(":status", "200")])
    writer.write(connection.data_to_send())
    await writer.drain()

    for payload in payloads:
        encoded = frame.encode_frame(payload)
        midpoint = max(1, len(encoded) // 2)
        for chunk in (encoded[:midpoint], encoded[midpoint:]):
            if not chunk:
                continue
            connection.send_data(stream_id, chunk)
            writer.write(connection.data_to_send())
            await writer.drain()
            # Give the client a chance to actually read this chunk before the next one
            # lands, so the split is a real partial-frame-over-the-socket case.
            await asyncio.sleep(0.01)

    connection.end_stream(stream_id)
    writer.write(connection.data_to_send())
    await writer.drain()


async def _respond_not_found(
    connection: h2.connection.H2Connection,
    writer: asyncio.StreamWriter,
    stream_id: int,
) -> None:
    """Respond 404 with no body."""
    connection.send_headers(stream_id, [(":status", "404")], end_stream=True)
    writer.write(connection.data_to_send())
    await writer.drain()


async def _stream_forever(
    connection: h2.connection.H2Connection,
    writer: asyncio.StreamWriter,
    stream_id: int,
) -> None:
    """Respond 200, then keep streaming small frames until the client disconnects."""
    connection.send_headers(stream_id, [(":status", "200")])
    writer.write(connection.data_to_send())
    await writer.drain()
    counter = 0
    while True:
        counter += 1
        connection.send_data(stream_id, frame.encode_frame(f"tick-{counter}".encode()))
        writer.write(connection.data_to_send())
        await writer.drain()
        await asyncio.sleep(0.01)


async def test_subscriber_yields_frames_in_order_over_real_socket() -> None:
    payloads = [b"first", b"second-payload", b"", b"x" * 4000]

    async def handler(
        connection: h2.connection.H2Connection,
        writer: asyncio.StreamWriter,
        stream_id: int,
        path: str,
    ) -> None:
        assert path == "/topic"
        await _stream_payloads(connection, writer, stream_id, payloads)

    async with _FakeH2Server(handler) as address:
        async with subscriber.Subscriber(address, "/topic") as topic_subscriber:
            received = [payload async for payload in topic_subscriber]

        assert received == payloads


async def test_subscriber_raises_connection_error_on_non_200_response() -> None:
    async def handler(
        connection: h2.connection.H2Connection,
        writer: asyncio.StreamWriter,
        stream_id: int,
        path: str,
    ) -> None:
        del path
        await _respond_not_found(connection, writer, stream_id)

    async with (
        _FakeH2Server(handler) as address,
        subscriber.Subscriber(address, "/missing") as topic_subscriber,
    ):
        with pytest.raises(ConnectionError, match=r"status b'404'"):
            async for _ in topic_subscriber:
                pass


async def test_subscriber_stops_cleanly_when_stream_ends() -> None:
    payloads = [b"only-frame"]

    async def handler(
        connection: h2.connection.H2Connection,
        writer: asyncio.StreamWriter,
        stream_id: int,
        path: str,
    ) -> None:
        del path
        await _stream_payloads(connection, writer, stream_id, payloads)

    async with (
        _FakeH2Server(handler) as address,
        subscriber.Subscriber(address, "/topic") as topic_subscriber,
    ):
        received = [payload async for payload in topic_subscriber]
        assert received == payloads

        # The iterator is exhausted: advancing it again must not hang or reconnect.
        with pytest.raises(StopAsyncIteration):
            await topic_subscriber.__anext__()


_FRAMES_BEFORE_ACLOSE = 2
_ACLOSE_WAIT_TIMEOUT_SECONDS = 2


async def test_aclose_stops_iteration_mid_stream() -> None:
    received: list[bytes] = []
    got_enough_frames = asyncio.Event()

    async def handler(
        connection: h2.connection.H2Connection,
        writer: asyncio.StreamWriter,
        stream_id: int,
        path: str,
    ) -> None:
        del path
        await _stream_forever(connection, writer, stream_id)

    async def consume(topic_subscriber: subscriber.Subscriber) -> None:
        async for payload in topic_subscriber:
            received.append(payload)
            if len(received) >= _FRAMES_BEFORE_ACLOSE:
                got_enough_frames.set()

    async with _FakeH2Server(handler) as address:
        topic_subscriber = subscriber.Subscriber(address, "/topic")
        task = asyncio.ensure_future(consume(topic_subscriber))
        await asyncio.wait_for(got_enough_frames.wait(), timeout=_ACLOSE_WAIT_TIMEOUT_SECONDS)

        await topic_subscriber.aclose()
        await asyncio.wait_for(task, timeout=_ACLOSE_WAIT_TIMEOUT_SECONDS)

        # Proves aclose() actually tore down the transport, not just that iteration
        # happened to stop for some other reason.
        assert topic_subscriber._active is None

    assert len(received) >= _FRAMES_BEFORE_ACLOSE


async def test_subscriber_rejects_address_without_port() -> None:
    with pytest.raises(ValueError, match="host:port"):
        subscriber.Subscriber("no-port-here", "/topic")


# `_parse_address`, `_build_request_headers`, `_extract_status`, and
# `_is_stream_ending_event` are the pure helpers `Subscriber` wraps; tested directly here
# since none of them need a connection, a socket, or an event loop to exercise exhaustively.


def test_parse_address_splits_host_and_port() -> None:
    assert subscriber._parse_address("127.0.0.1:8081") == ("127.0.0.1", 8081)


def test_parse_address_rejects_missing_port() -> None:
    with pytest.raises(ValueError, match="host:port"):
        subscriber._parse_address("no-port-here")


def test_build_request_headers() -> None:
    assert subscriber._build_request_headers("/sensor/imu", "127.0.0.1:8081") == [
        (":method", "GET"),
        (":path", "/sensor/imu"),
        (":scheme", "http"),
        (":authority", "127.0.0.1:8081"),
    ]


def test_extract_status_found() -> None:
    headers = [(b":status", b"200"), (b"content-type", b"application/octet-stream")]
    assert subscriber._extract_status(headers) == b"200"


def test_extract_status_missing() -> None:
    assert subscriber._extract_status([(b"content-type", b"application/octet-stream")]) is None


def test_extract_status_empty_headers() -> None:
    assert subscriber._extract_status([]) is None


def test_is_stream_ending_event_true_for_stream_ended() -> None:
    assert subscriber._is_stream_ending_event(h2.events.StreamEnded(stream_id=1))


def test_is_stream_ending_event_true_for_stream_reset() -> None:
    assert subscriber._is_stream_ending_event(h2.events.StreamReset(stream_id=1))


def test_is_stream_ending_event_true_for_connection_terminated() -> None:
    assert subscriber._is_stream_ending_event(h2.events.ConnectionTerminated())


def test_is_stream_ending_event_false_for_other_events() -> None:
    assert not subscriber._is_stream_ending_event(h2.events.ResponseReceived(stream_id=1))
