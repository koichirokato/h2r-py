"""Node: the user-facing entry point tying the publisher, subscriber, and peer registry together."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Callable

    from h2r import publisher as publisher_module
    from h2r import subscriber as subscriber_module


class Node:
    """An h2r node that can advertise topics and subscribe to peers' topics."""

    def __init__(
        self,
        name: str,
        port: int,
        registry_path: str | pathlib.Path = "peers.yaml",
    ) -> None:
        """Create a node named *name*, publishing on *port*, resolving peers via *registry_path*."""
        raise NotImplementedError

    def advertise(self, topic: str, message_type: str) -> publisher_module.Publisher:
        """Advertise *topic* and return its :class:`~h2r.publisher.Publisher`."""
        raise NotImplementedError

    def subscribe(
        self,
        topic: str,
        callback: Callable[[bytes], None],
    ) -> subscriber_module.Subscriber:
        """Resolve *topic* in the peer registry and subscribe, invoking *callback* per message."""
        raise NotImplementedError

    async def spin(self) -> None:
        """Run the node's publisher and subscriber event loops until cancelled."""
        raise NotImplementedError
