"""Minimal publisher: advertises one topic and publishes a message every second."""

from __future__ import annotations

import asyncio
import itertools

from h2r import publisher

_HOST = "127.0.0.1"
_PORT = 8081
_TOPIC = "/counter"

# How long to wait for `serve()` to fail fast (e.g. address already in use) before assuming
# the bind succeeded and it's now blocked on `aclose()`. See `main`'s comment below.
_STARTUP_TIMEOUT_SECONDS = 0.5


async def main() -> None:
    """Serve `_TOPIC`, publishing an incrementing counter once a second."""
    node = publisher.Publisher(_HOST, _PORT)
    node.advertise(_TOPIC, "text/plain")
    serve_task = asyncio.ensure_future(node.serve())

    # `serve()` only returns once `aclose()` is called, so if it finishes this early the
    # startup itself failed (e.g. `OSError: address already in use`). Surface that now
    # instead of printing a false "publishing ..." success and looping forever.
    await asyncio.wait([serve_task], timeout=_STARTUP_TIMEOUT_SECONDS)
    if serve_task.done():
        await serve_task  # re-raises the startup failure.
        msg = "publisher stopped before it started serving"
        raise RuntimeError(msg)

    print(f"publishing {_TOPIC} on {_HOST}:{node.port}")

    # h2r is a broadcast, no-replay pub/sub: `publish()` only reaches subscribers that have
    # already completed their HTTP/2 handshake at call time, and nothing is buffered for
    # latecomers. So if `subscriber_example.py` is started after the loop below has already
    # begun, its first printed message won't be "0" -- it'll be whatever count was first
    # published after the subscriber finished connecting. That's expected protocol behavior,
    # not a bug (see README.md's "Pub/sub model" section).
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
