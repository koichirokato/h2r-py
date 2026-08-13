"""HTTP/2 publisher (server): advertises topics and broadcasts frames to subscribers.

Payload-agnostic by design: this layer moves ``bytes`` only. A topic's message type is
carried as a string in the ``x-h2r-type`` response header (matching h2r-core's C-API
shape), not encoded into the transport. Serialization (e.g. protobuf) belongs in a layer
built on top of this one.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import h2.config
import h2.connection
import h2.events
import h2.exceptions

from h2r import frame

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterable
    from collections.abc import Mapping

# Chunk size for reads off the raw socket; independent of HTTP/2 frame sizing.
_READ_CHUNK_SIZE = 65536

# Bound on each subscriber's outbound queue. Small and deliberately so: it exists purely
# to absorb brief scheduling jitter between `publish()` and a stream's drain loop, not to
# buffer a slow consumer. See `_Subscription` for the drop policy this backs.
_SUBSCRIBER_QUEUE_MAXSIZE = 64


def _decode_header(value: bytes | str) -> str:
    """Normalize an inbound header field to `str` regardless of h2's static `bytes` typing."""
    return value if isinstance(value, str) else value.decode("utf-8")


def _headers_to_dict(headers: Iterable[tuple[bytes | str, bytes | str]]) -> dict[str, str]:
    """Decode an h2 event's header list into a plain ``str`` -> ``str`` mapping.

    Pure: the result depends only on *headers*.
    """
    return {_decode_header(name): _decode_header(value) for name, value in headers}


def _response_headers_for(message_type: str | None) -> list[tuple[str, str]]:
    """Build the HTTP/2 response headers for a request against an advertised topic.

    *message_type* is the topic's registered ``x-h2r-type`` value, or ``None`` if the
    requested topic isn't advertised — in which case these are 404 headers instead of 200's.

    Pure: the result depends only on *message_type*.
    """
    if message_type is None:
        return [(":status", "404")]
    return [
        (":status", "200"),
        ("content-type", "application/octet-stream"),
        ("x-h2r-type", message_type),
    ]


def _next_chunk_size(remaining_length: int, window: int, max_frame_size: int) -> int:
    """Return how many bytes of a payload to send in the next DATA frame.

    Bounded by the flow-control *window*, the connection's *max_frame_size*, and how much
    payload (*remaining_length*) is actually left to send.

    Pure: the result depends only on its three arguments.
    """
    return min(remaining_length, window, max_frame_size)


class _Subscription:
    """One connected subscriber stream: a bounded outbound queue plus a flow-control wakeup.

    The queue is bounded so a subscriber that isn't draining fast enough cannot make
    `Publisher.publish` block or grow memory without limit; `publish` drops the frame for
    that subscriber instead (see its docstring). `window_available` is set whenever an
    HTTP/2 WindowUpdated event might have freed up room to send, so the drain loop can
    `await` it instead of busy-polling `local_flow_control_window`.
    """

    def __init__(self) -> None:
        """Create a subscription with an empty queue and an initially-available window."""
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_MAXSIZE)
        self.window_available: asyncio.Event = asyncio.Event()
        self.window_available.set()


class _ConnectionHandler:
    """Speaks HTTP/2 over one accepted TCP connection and serves advertised topics on it.

    Owns the connection's `h2.connection.H2Connection` state machine plus, for every stream
    on this connection that turned into a topic subscription, the `_Subscription` and the
    task draining it onto the wire. Registration/unregistration of subscribers into the
    publisher's shared topic tables is delegated to the callables passed in, so this class
    never touches `Publisher` internals directly.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        advertised: Mapping[str, str],
        register_subscriber: Callable[[str, _Subscription], None],
        unregister_subscriber: Callable[[str, _Subscription], None],
    ) -> None:
        """Wrap *reader*/*writer* for one connection; *advertised* is the live topic table."""
        self._reader = reader
        self._writer = writer
        self._advertised = advertised
        self._register_subscriber = register_subscriber
        self._unregister_subscriber = unregister_subscriber
        self._connection = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=False, header_encoding="utf-8"),
        )
        self._stream_subscriptions: dict[int, tuple[str, _Subscription]] = {}
        self._drain_tasks: dict[int, asyncio.Task[None]] = {}

    async def run(self) -> None:
        """Drive this connection until the peer disconnects or the connection errors out."""
        self._connection.initiate_connection()
        self._flush()
        await self._writer.drain()
        try:
            while True:
                data = await self._reader.read(_READ_CHUNK_SIZE)
                if not data:
                    break
                try:
                    events = self._connection.receive_data(data)
                except h2.exceptions.ProtocolError:
                    break
                terminated = await self._handle_events(events)
                self._flush()
                await self._writer.drain()
                if terminated:
                    break
        except (ConnectionResetError, OSError):
            pass
        finally:
            await self._close()

    async def _handle_events(self, events: list[h2.events.Event]) -> bool:
        """Dispatch *events* from one `receive_data` call; return whether the peer hung up."""
        for event in events:
            if isinstance(event, h2.events.RequestReceived):
                self._handle_request_received(event)
            elif isinstance(event, h2.events.WindowUpdated):
                self._handle_window_updated(event)
            elif isinstance(event, h2.events.StreamReset):
                await self._unregister_stream(event.stream_id)
            elif isinstance(event, h2.events.ConnectionTerminated):
                return True
        return False

    def _flush(self) -> None:
        """Write out any bytes the last connection mutation queued up for sending."""
        outbound = self._connection.data_to_send()
        if outbound:
            self._writer.write(outbound)

    def _handle_request_received(self, event: h2.events.RequestReceived) -> None:
        """Answer a new stream: 200 + subscribe for an advertised topic, else 404."""
        # h2's `Header` type is statically `bytes`-only even though `header_encoding="utf-8"`
        # decodes to `str` at runtime; normalize explicitly so downstream typing lines up.
        headers = _headers_to_dict(event.headers)
        topic = headers.get(":path", "")
        message_type = self._advertised.get(topic)
        response_headers = _response_headers_for(message_type)
        if message_type is None:
            self._connection.send_headers(event.stream_id, response_headers, end_stream=True)
            return

        self._connection.send_headers(event.stream_id, response_headers)
        subscription = _Subscription()
        self._register_subscriber(topic, subscription)
        self._stream_subscriptions[event.stream_id] = (topic, subscription)
        self._drain_tasks[event.stream_id] = asyncio.create_task(
            self._drain_subscription(event.stream_id, subscription),
        )

    def _handle_window_updated(self, event: h2.events.WindowUpdated) -> None:
        """Wake the drain loop(s) that may now have room to send."""
        if event.stream_id == 0:
            for _, subscription in self._stream_subscriptions.values():
                subscription.window_available.set()
            return
        entry = self._stream_subscriptions.get(event.stream_id)
        if entry is not None:
            entry[1].window_available.set()

    async def _drain_subscription(self, stream_id: int, subscription: _Subscription) -> None:
        """Forward queued frames from *subscription* onto *stream_id* until it closes.

        Runs as its own task per subscriber stream, so one slow reader only stalls its own
        drain loop (and starts losing frames per `Publisher.publish`'s drop policy) rather
        than the connection's shared read loop.
        """
        try:
            while True:
                payload = await subscription.queue.get()
                await self._send_framed(stream_id, payload)
        except h2.exceptions.H2Error:
            # Stream closed/reset out from under us; the read loop's event handling (or
            # connection teardown) is responsible for unregistering the subscription.
            pass

    async def _send_framed(self, stream_id: int, payload: bytes) -> None:
        """Send *payload* as one or more DATA frames, chunked to fit the flow-control window."""
        view = memoryview(payload)
        while view:
            window = self._connection.local_flow_control_window(stream_id)
            chunk_size = _next_chunk_size(
                len(view),
                window,
                self._connection.max_outbound_frame_size,
            )
            if chunk_size <= 0:
                subscription = self._stream_subscriptions[stream_id][1]
                subscription.window_available.clear()
                await subscription.window_available.wait()
                continue
            self._connection.send_data(stream_id, bytes(view[:chunk_size]))
            self._flush()
            await self._writer.drain()
            view = view[chunk_size:]

    async def _unregister_stream(self, stream_id: int) -> None:
        """Cancel *stream_id*'s drain task and remove it from the shared topic table."""
        task = self._drain_tasks.pop(stream_id, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        entry = self._stream_subscriptions.pop(stream_id, None)
        if entry is not None:
            topic, subscription = entry
            self._unregister_subscriber(topic, subscription)

    async def _close(self) -> None:
        """Tear down every stream on this connection, then close the socket."""
        for stream_id in list(self._drain_tasks):
            await self._unregister_stream(stream_id)
        with contextlib.suppress(h2.exceptions.ProtocolError, OSError):
            self._connection.close_connection()
            self._flush()
            await self._writer.drain()
        self._writer.close()
        with contextlib.suppress(OSError):
            await self._writer.wait_closed()


class Publisher:
    """Serves topics over HTTP/2, broadcasting published bytes to every connected subscriber.

    Each advertised topic is bound to an HTTP/2 GET endpoint. Each incoming connection gets
    its own bounded queue fed by :meth:`publish`; a subscriber that falls too far behind has
    its queue reset rather than blocking the publisher, mirroring h2r-core's fan-out over
    ``tokio::sync::broadcast``.
    """

    def __init__(self, host: str, port: int) -> None:
        """Bind to *host*:*port*. Call :meth:`serve` to start accepting connections."""
        self._host = host
        self._port = port
        self._advertised: dict[str, str] = {}
        self._subscribers: dict[str, set[_Subscription]] = {}
        self._server: asyncio.Server | None = None
        self._connection_tasks: set[asyncio.Task[None]] = set()
        self._closed_event: asyncio.Event = asyncio.Event()

    @property
    def port(self) -> int:
        """The bound TCP port; resolves the actual ephemeral port once `serve` is listening."""
        if self._server is not None and self._server.sockets:
            return int(self._server.sockets[0].getsockname()[1])
        return self._port

    def advertise(self, topic: str, message_type: str) -> None:
        """Register *topic*; new connections receive ``x-h2r-type: message_type``."""
        self._advertised[topic] = message_type

    def publish(self, topic: str, payload: bytes) -> None:
        """Frame *payload* (see :mod:`h2r.frame`) and broadcast it to *topic*'s subscribers.

        Synchronous and O(1) in the number of queued bytes: it hands the framed payload to
        each subscriber's bounded queue via `put_nowait` and returns immediately. A
        subscriber whose queue is already full (i.e. its drain loop isn't keeping up with
        the connection) has this frame dropped for it rather than backpressuring or
        blocking the caller — matching h2r-core's `tokio::sync::broadcast` semantics, where
        a slow receiver lags and misses messages instead of slowing down the sender.

        Must be called from the same thread running the `serve` event loop: it touches
        `asyncio.Queue` objects synchronously without `call_soon_threadsafe`, which is only
        safe from that loop's own thread.
        """
        subscribers = self._subscribers.get(topic)
        if not subscribers:
            return
        framed = frame.encode_frame(payload)
        for subscription in subscribers:
            with contextlib.suppress(asyncio.QueueFull):
                subscription.queue.put_nowait(framed)

    async def serve(self) -> None:
        """Accept connections and serve advertised topics until :meth:`aclose` is called."""
        self._server = await asyncio.start_server(self._on_connection, self._host, self._port)
        async with self._server:
            await self._closed_event.wait()

    async def aclose(self) -> None:
        """Stop accepting new connections and close every open stream."""
        self._closed_event.set()
        if self._server is not None:
            self._server.close()
        tasks = list(self._connection_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()

    def _on_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Start serving one freshly-accepted connection as a tracked background task."""
        task = asyncio.create_task(self._handle_connection(reader, writer))
        self._connection_tasks.add(task)
        task.add_done_callback(self._connection_tasks.discard)

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Run one connection's HTTP/2 session to completion."""
        handler = _ConnectionHandler(
            reader,
            writer,
            self._advertised,
            self._register_subscriber,
            self._unregister_subscriber,
        )
        await handler.run()

    def _register_subscriber(self, topic: str, subscription: _Subscription) -> None:
        """Add *subscription* to *topic*'s fan-out set so `publish` reaches it."""
        self._subscribers.setdefault(topic, set()).add(subscription)

    def _unregister_subscriber(self, topic: str, subscription: _Subscription) -> None:
        """Remove *subscription* from *topic*'s fan-out set (no-op if already gone)."""
        subscribers = self._subscribers.get(topic)
        if subscribers is not None:
            subscribers.discard(subscription)
