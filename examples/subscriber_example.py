"""Minimal subscriber: connects to `publisher_example.py` and prints every message."""

from __future__ import annotations

import asyncio

from h2r import subscriber

_PUBLISHER_ADDRESS = "127.0.0.1:8081"
_TOPIC = "/counter"


async def main() -> None:
    """Subscribe to `_TOPIC` and print each received payload until interrupted."""
    async with subscriber.Subscriber(_PUBLISHER_ADDRESS, _TOPIC) as topic_subscriber:
        async for payload in topic_subscriber:
            print(f"received {payload.decode()}")


if __name__ == "__main__":
    asyncio.run(main())
