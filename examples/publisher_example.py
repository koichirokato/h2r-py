"""Minimal publisher: advertises one topic and publishes a message every second."""

from __future__ import annotations

import asyncio
import itertools

from h2r import publisher

_HOST = "127.0.0.1"
_PORT = 8081
_TOPIC = "/counter"


async def main() -> None:
    """Serve `_TOPIC`, publishing an incrementing counter once a second."""
    node = publisher.Publisher(_HOST, _PORT)
    node.advertise(_TOPIC, "text/plain")
    serve_task = asyncio.ensure_future(node.serve())
    print(f"publishing {_TOPIC} on {_HOST}:{node.port}")

    try:
        for count in itertools.count():
            node.publish(_TOPIC, str(count).encode())
            print(f"published {count}")
            await asyncio.sleep(1)
    finally:
        await node.aclose()
        await serve_task


if __name__ == "__main__":
    asyncio.run(main())
