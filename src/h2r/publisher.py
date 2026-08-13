"""HTTP/2 publisher (server): advertises topics and broadcasts frames to subscribers.

Payload-agnostic by design: this layer moves ``bytes`` only. A topic's message type is
carried as a string in the ``x-h2r-type`` response header (matching h2r-core's C-API
shape), not encoded into the transport. Serialization (e.g. protobuf) belongs in a layer
built on top of this one.
"""

from __future__ import annotations


class Publisher:
    """Serves topics over HTTP/2, broadcasting published bytes to every connected subscriber.

    Each advertised topic is bound to an HTTP/2 GET endpoint. Each incoming connection gets
    its own bounded queue fed by :meth:`publish`; a subscriber that falls too far behind has
    its queue reset rather than blocking the publisher, mirroring h2r-core's fan-out over
    ``tokio::sync::broadcast``.
    """

    def __init__(self, host: str, port: int) -> None:
        """Bind to *host*:*port*. Call :meth:`serve` to start accepting connections."""
        raise NotImplementedError

    def advertise(self, topic: str, message_type: str) -> None:
        """Register *topic*; new connections receive ``x-h2r-type: message_type``."""
        raise NotImplementedError

    def publish(self, topic: str, payload: bytes) -> None:
        """Frame *payload* (see :mod:`h2r.frame`) and broadcast it to *topic*'s subscribers."""
        raise NotImplementedError

    async def serve(self) -> None:
        """Accept connections and serve advertised topics until cancelled."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Stop accepting connections and close every open stream."""
        raise NotImplementedError
