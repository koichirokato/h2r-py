"""HTTP/2 subscriber (client): opens a long-lived GET and decodes frames.

Payload-agnostic by design, matching :mod:`h2r.publisher`: this layer yields ``bytes``
only. The publisher's ``x-h2r-type`` response header is the caller's signal for how to
deserialize a payload; decoding into a typed message belongs in a layer built on top of
this one.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import dataclasses
from typing import TYPE_CHECKING
from typing import Self

import h2.config
import h2.connection
import h2.events

from h2r import frame

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from collections.abc import Iterable
    from types import TracebackType

_READ_CHUNK_SIZE = 65536
"""Bytes requested per socket read; frames are reassembled regardless of chunking."""

_STREAM_ENDING_EVENTS = (
    h2.events.StreamEnded,
    h2.events.StreamReset,
    h2.events.ConnectionTerminated,
)


def _parse_address(address: str) -> tuple[str, int]:
    """Split a ``host:port`` address string into its parts.

    Pure: the result (or the :class:`ValueError` raised for a malformed *address*)
    depends only on *address*.
    """
    host, separator, port_text = address.rpartition(":")
    if not separator:
        message = f"publisher_address must be 'host:port', got {address!r}"
        raise ValueError(message)
    return host, int(port_text)


def _build_request_headers(topic: str, authority: str) -> list[tuple[str, str]]:
    """Build the HTTP/2 request headers for a subscribe ``GET`` to *topic*.

    Pure: the result depends only on *topic* and *authority*.
    """
    return [
        (":method", "GET"),
        (":path", topic),
        (":scheme", "http"),
        (":authority", authority),
    ]


def _extract_status(headers: Iterable[tuple[bytes, bytes]]) -> bytes | None:
    """Return the ``:status`` value from a response's *headers*, or ``None`` if absent.

    Pure: the result depends only on *headers*.
    """
    return next((value for name, value in headers if name == b":status"), None)


def _is_stream_ending_event(event: h2.events.Event) -> bool:
    """Return whether *event* means the stream is over (cleanly, reset, or torn down).

    Pure: the result depends only on *event*'s type.
    """
    return isinstance(event, _STREAM_ENDING_EVENTS)


@dataclasses.dataclass
class _ActiveConnection:
    """The sockets and protocol state for one open subscription."""

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    h2_connection: h2.connection.H2Connection


class Subscriber:
    """Connects to a publisher and yields decoded frame payloads for one topic.

    Opens a single long-lived HTTP/2 GET request to *publisher_address* + *topic* and
    incrementally decodes length-delimited frames (see :mod:`h2r.frame`) from the streaming
    response body. The connection is opened lazily, on the first iteration step.

    Iteration ends cleanly (``StopAsyncIteration``, no exception) when the publisher ends the
    stream, resets it, or the connection is otherwise lost. If the publisher responds with a
    non-200 status (e.g. an unknown topic), the first iteration step raises
    :class:`ConnectionError` instead.

    For deterministic cleanup when breaking out of iteration early, call :meth:`aclose` (or
    use the subscriber as an async context manager, which calls it automatically). Without
    that, the socket is only closed when the object is garbage collected.
    """

    def __init__(self, publisher_address: str, topic: str) -> None:
        """Prepare a subscription to *topic* on the publisher at *publisher_address*.

        *publisher_address* must be ``host:port``. The connection isn't opened here; it's
        opened lazily on the first iteration step.
        """
        self._host, self._port = _parse_address(publisher_address)
        self._authority = publisher_address
        self._topic = topic

        self._active: _ActiveConnection | None = None
        self._decoder = frame.FrameDecoder()
        self._pending_frames: collections.deque[bytes] = collections.deque()
        self._started = False
        self._closed = False

    def __aiter__(self) -> AsyncIterator[bytes]:
        """Return self; frame-by-frame advancement happens in :meth:`__anext__`."""
        return self

    async def __anext__(self) -> bytes:
        """Return the next decoded frame payload, connecting first if not yet started."""
        if self._closed:
            raise StopAsyncIteration
        if not self._started:
            await self._connect()
            self._started = True

        while not self._pending_frames:
            active = self._active
            if self._closed or active is None:
                raise StopAsyncIteration
            data = await active.reader.read(_READ_CHUNK_SIZE)
            if self._closed:
                # aclose() ran concurrently with the read above; its buffers are gone.
                raise StopAsyncIteration
            if not data:
                await self._finish()
                break
            try:
                stream_ended = self._handle_received_data(active, data)
            except BaseException:
                # Includes a non-200 status ConnectionError: don't leak the transport
                # just because the caller isn't iterating inside `async with`.
                await self._finish()
                raise
            await self._flush_outgoing(active)
            if stream_ended:
                await self._finish()
                break

        if not self._pending_frames:
            raise StopAsyncIteration
        return self._pending_frames.popleft()

    async def aclose(self) -> None:
        """Cancel the subscription and close the connection.

        Safe to call multiple times, and safe to call while a read is in flight elsewhere:
        closing the transport unblocks that read with EOF.
        """
        self._closed = True
        await self._close_transport()

    async def __aenter__(self) -> Self:
        """Return self for use in an ``async with`` block."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the subscription when the ``async with`` block exits."""
        del exc_type, exc_value, traceback
        await self.aclose()

    async def _connect(self) -> None:
        """Open the TCP connection, send the HTTP/2 preface, and issue the GET request."""
        reader, writer = await asyncio.open_connection(self._host, self._port)

        h2_connection = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=True),
        )
        h2_connection.initiate_connection()
        stream_id = h2_connection.get_next_available_stream_id()
        h2_connection.send_headers(
            stream_id,
            _build_request_headers(self._topic, self._authority),
            end_stream=True,
        )
        active = _ActiveConnection(reader=reader, writer=writer, h2_connection=h2_connection)
        self._active = active
        active.writer.write(h2_connection.data_to_send())
        await active.writer.drain()

    def _handle_received_data(self, active: _ActiveConnection, data: bytes) -> bool:
        """Feed *data* to the H2 connection and the frame decoder.

        Returns whether the stream ended (cleanly or via reset). Raises
        :class:`ConnectionError` if the publisher's response status wasn't 200.
        """
        stream_ended = False
        for event in active.h2_connection.receive_data(data):
            if isinstance(event, h2.events.ResponseReceived):
                status = _extract_status(event.headers)
                if status != b"200":
                    message = (
                        f"publisher rejected subscription to {self._topic!r}: status {status!r}"
                    )
                    raise ConnectionError(message)
            elif isinstance(event, h2.events.DataReceived):
                active.h2_connection.acknowledge_received_data(
                    event.flow_controlled_length,
                    event.stream_id,
                )
                self._pending_frames.extend(self._decoder.feed(event.data))
            elif _is_stream_ending_event(event):
                stream_ended = True
        return stream_ended

    async def _flush_outgoing(self, active: _ActiveConnection) -> None:
        """Write and drain any bytes the H2 connection has queued (e.g. flow-control acks)."""
        outgoing = active.h2_connection.data_to_send()
        if outgoing:
            active.writer.write(outgoing)
            await active.writer.drain()

    async def _finish(self) -> None:
        """Mark the subscription closed and tear down the transport."""
        self._closed = True
        await self._close_transport()

    async def _close_transport(self) -> None:
        """Close the writer (and its underlying socket) if still open."""
        active, self._active = self._active, None
        if active is None:
            return
        writer = active.writer
        if not writer.is_closing():
            writer.close()
        with contextlib.suppress(OSError, ConnectionError):
            await writer.wait_closed()
