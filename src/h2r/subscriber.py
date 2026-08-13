"""HTTP/2 subscriber (client): opens a long-lived GET and decodes frames.

Payload-agnostic by design, matching :mod:`h2r.publisher`: this layer yields ``bytes``
only. The publisher's ``x-h2r-type`` response header is the caller's signal for how to
deserialize a payload; decoding into a typed message belongs in a layer built on top of
this one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class Subscriber:
    """Connects to a publisher and yields decoded frame payloads for one topic.

    Opens a single long-lived HTTP/2 GET request to *publisher_address* + *topic* and
    incrementally decodes length-delimited frames (see :mod:`h2r.frame`) from the streaming
    response body.
    """

    def __init__(self, publisher_address: str, topic: str) -> None:
        """Prepare a subscription to *topic* on the publisher at *publisher_address*."""
        raise NotImplementedError

    def __aiter__(self) -> AsyncIterator[bytes]:
        """Yield decoded frame payloads as they arrive, until :meth:`aclose` or disconnect."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Cancel the subscription and close the connection."""
        raise NotImplementedError
