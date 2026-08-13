"""End-to-end tests for h2r.publisher against a real, independent HTTP/2 client.

These deliberately don't mock h2 or the socket layer: httpx is a separately-implemented
HTTP/2 client, so a passing test here is evidence the publisher's wire behavior actually
interoperates with another implementation, not just that it's internally self-consistent.
Decoding the response bytes with `h2r.frame.FrameDecoder` then checks our own encoder's
output the same way a real subscriber would.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import httpx
import pytest

from h2r import frame
from h2r import publisher as publisher_module

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_TOPIC = "/sensor/imu"
_MESSAGE_TYPE = "sensor_msgs/Imu"
_READ_TIMEOUT = 5.0


@contextlib.asynccontextmanager
async def _running_publisher() -> AsyncIterator[publisher_module.Publisher]:
    """Serve a `Publisher` on an ephemeral port for the duration of the `with` block."""
    server = publisher_module.Publisher("127.0.0.1", 0)
    server.advertise(_TOPIC, _MESSAGE_TYPE)
    task = asyncio.create_task(server.serve())
    for _ in range(10_000):
        if server.port:
            break
        await asyncio.sleep(0)
    try:
        yield server
    finally:
        await server.aclose()
        await task


def _client(base_url: str) -> httpx.AsyncClient:
    """Build an HTTP/2-prior-knowledge client (no TLS, so ALPN can't negotiate h2 for us)."""
    return httpx.AsyncClient(base_url=base_url, http1=False, http2=True)


async def _next_frame(body: AsyncIterator[bytes], decoder: frame.FrameDecoder) -> bytes:
    """Pull chunks from *body* until `decoder` completes a frame, and return its payload."""
    async for chunk in body:
        frames = decoder.feed(chunk)
        if frames:
            return frames[0]
    pytest.fail("subscriber stream ended before a frame arrived")


async def test_unknown_topic_returns_404() -> None:
    async with (
        _running_publisher() as server,
        _client(f"http://127.0.0.1:{server.port}") as client,
    ):
        response = await client.get("/no/such/topic")
    assert response.status_code == httpx.codes.NOT_FOUND


async def test_subscriber_receives_framed_payload_and_type_header() -> None:
    payload = b"\x01\x02\x03imu-sample"
    async with (
        _running_publisher() as server,
        _client(f"http://127.0.0.1:{server.port}") as client,
        client.stream("GET", _TOPIC) as response,
    ):
        assert response.status_code == httpx.codes.OK
        assert response.http_version == "HTTP/2"
        assert response.headers["x-h2r-type"] == _MESSAGE_TYPE

        server.publish(_TOPIC, payload)

        decoder = frame.FrameDecoder()
        received = await asyncio.wait_for(
            _next_frame(response.aiter_bytes(), decoder),
            timeout=_READ_TIMEOUT,
        )
    assert received == payload


async def test_publish_to_topic_with_no_subscribers_does_not_raise() -> None:
    async with _running_publisher() as server:
        server.publish(_TOPIC, b"nobody is listening")


async def test_multiple_concurrent_subscribers_receive_same_frame() -> None:
    payload = b"broadcast-me"
    async with _running_publisher() as server:
        base_url = f"http://127.0.0.1:{server.port}"
        async with (
            _client(base_url) as client_a,
            _client(base_url) as client_b,
            client_a.stream("GET", _TOPIC) as response_a,
            client_b.stream("GET", _TOPIC) as response_b,
        ):
            assert response_a.status_code == httpx.codes.OK
            assert response_b.status_code == httpx.codes.OK
            assert response_a.headers["x-h2r-type"] == _MESSAGE_TYPE
            assert response_b.headers["x-h2r-type"] == _MESSAGE_TYPE

            server.publish(_TOPIC, payload)

            decoder_a = frame.FrameDecoder()
            decoder_b = frame.FrameDecoder()
            received_a, received_b = await asyncio.wait_for(
                asyncio.gather(
                    _next_frame(response_a.aiter_bytes(), decoder_a),
                    _next_frame(response_b.aiter_bytes(), decoder_b),
                ),
                timeout=_READ_TIMEOUT,
            )
    assert received_a == payload
    assert received_b == payload


async def test_aclose_terminates_open_subscriber_streams() -> None:
    payload = b"last-one-before-shutdown"
    server = publisher_module.Publisher("127.0.0.1", 0)
    server.advertise(_TOPIC, _MESSAGE_TYPE)
    task = asyncio.create_task(server.serve())
    for _ in range(10_000):
        if server.port:
            break
        await asyncio.sleep(0)

    async with (
        _client(f"http://127.0.0.1:{server.port}") as client,
        client.stream("GET", _TOPIC) as response,
    ):
        assert response.status_code == httpx.codes.OK

        server.publish(_TOPIC, payload)
        decoder = frame.FrameDecoder()
        body = response.aiter_bytes()
        received = await asyncio.wait_for(_next_frame(body, decoder), timeout=_READ_TIMEOUT)
        assert received == payload

        await asyncio.wait_for(server.aclose(), timeout=_READ_TIMEOUT)

        # The server closed the connection: the still-open body iterator must end rather
        # than hang, whether that surfaces as a clean EOF or a torn-down-mid-stream error.
        with contextlib.suppress(httpx.RemoteProtocolError, httpx.ReadError):
            await asyncio.wait_for(_assert_body_ends_without_more_data(body), timeout=_READ_TIMEOUT)

    await task


async def _assert_body_ends_without_more_data(body: AsyncIterator[bytes]) -> None:
    """Drain *body* to completion, failing loudly if it yields any further chunk."""
    async for _chunk in body:
        pytest.fail("body iterator produced data after aclose() instead of ending")


async def test_multiple_frames_arrive_in_order() -> None:
    payloads = [b"first", b"second", b"third"]
    async with (
        _running_publisher() as server,
        _client(f"http://127.0.0.1:{server.port}") as client,
        client.stream("GET", _TOPIC) as response,
    ):
        assert response.status_code == httpx.codes.OK

        decoder = frame.FrameDecoder()
        body = response.aiter_bytes()
        received = []
        for payload in payloads:
            server.publish(_TOPIC, payload)
            received.append(
                await asyncio.wait_for(_next_frame(body, decoder), timeout=_READ_TIMEOUT),
            )
    assert received == payloads
