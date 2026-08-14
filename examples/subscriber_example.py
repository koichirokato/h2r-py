"""Minimal subscriber: connects to `publisher_example.py` and prints every message."""

from __future__ import annotations

import asyncio
import itertools
from typing import TYPE_CHECKING

from h2r import subscriber

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_PUBLISHER_ADDRESS = "127.0.0.1:8081"
_TOPIC = "/counter"

# The README's "Try it" section starts this script right after backgrounding
# publisher_example.py, with no synchronization between the two. If the publisher hasn't
# finished `asyncio.start_server()` yet, the first connection attempt gets
# `ConnectionRefusedError`. Retry with a short backoff instead of failing the demo on that
# race; this bounds the wait to about 5 seconds before giving up for real.
_CONNECT_RETRY_ATTEMPTS = 10
_CONNECT_RETRY_DELAY_SECONDS = 0.5


async def _iter_with_connect_retry(
    topic_subscriber: subscriber.Subscriber,
) -> AsyncIterator[bytes]:
    """Yield payloads from *topic_subscriber*, retrying only the initial connection."""
    iterator = aiter(topic_subscriber)
    for attempt in itertools.count(1):
        try:
            first_payload = await anext(iterator)
        except StopAsyncIteration:
            return
        except ConnectionRefusedError:
            if attempt >= _CONNECT_RETRY_ATTEMPTS:
                raise
            await asyncio.sleep(_CONNECT_RETRY_DELAY_SECONDS)
        else:
            yield first_payload
            break
    async for payload in iterator:
        yield payload


async def main() -> None:
    """Subscribe to `_TOPIC` and print each received payload until interrupted."""
    async with subscriber.Subscriber(_PUBLISHER_ADDRESS, _TOPIC) as topic_subscriber:
        async for payload in _iter_with_connect_retry(topic_subscriber):
            print(f"received {payload.decode()}")


if __name__ == "__main__":
    asyncio.run(main())
