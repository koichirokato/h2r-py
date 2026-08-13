"""Static peer address book loaded from a YAML file.

Example::

    peers:
      sensor_node:
        addr: "127.0.0.1:8081"
        publishes:
          - /sensor/imu
          - /sensor/lidar
      control_node:
        addr: "127.0.0.1:8082"
        subscribes:
          - /cmd/velocity
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any

import yaml


@dataclasses.dataclass
class PeerEntry:
    """A single peer in the address book."""

    address: str
    """Network address (``host:port``)."""
    publishes: list[str] = dataclasses.field(default_factory=list)
    """Topics this peer publishes."""
    subscribes: list[str] = dataclasses.field(default_factory=list)
    """Topics this peer subscribes to."""


def _parse_peers(data: dict[str, Any] | None) -> dict[str, PeerEntry]:
    """Build the peer table from a parsed YAML document's top-level mapping.

    Pure: the result depends only on *data*.
    """
    peers: dict[str, PeerEntry] = {}
    for name, raw in ((data or {}).get("peers") or {}).items():
        peers[name] = PeerEntry(
            address=raw["addr"],
            publishes=raw.get("publishes") or [],
            subscribes=raw.get("subscribes") or [],
        )
    return peers


def _build_topic_index(peers: dict[str, PeerEntry]) -> dict[str, tuple[str, int]]:
    """Index *peers* by the topics they publish, resolved to ``(host, port)``.

    Pure: the result depends only on *peers*.
    """
    topic_address: dict[str, tuple[str, int]] = {}
    for entry in peers.values():
        host, _, port_str = entry.address.rpartition(":")
        port = int(port_str)
        for topic in entry.publishes:
            topic_address[topic] = (host, port)
    return topic_address


class PeerRegistry:
    """Loaded peer registry.

    Provides topic -> ``(host, port)`` resolution for the subscriber side.
    """

    def __init__(self, peers: dict[str, PeerEntry]) -> None:
        """Index *peers* by the topics they publish."""
        self._peers = peers
        self._topic_address = _build_topic_index(peers)

    @classmethod
    def from_file(cls, path: str | pathlib.Path) -> PeerRegistry:
        """Load the registry from a YAML file."""
        return cls.from_string(pathlib.Path(path).read_text())

    @classmethod
    def from_string(cls, yaml_text: str) -> PeerRegistry:
        """Parse the registry from a YAML string."""
        return cls(_parse_peers(yaml.safe_load(yaml_text)))

    @classmethod
    def empty(cls) -> PeerRegistry:
        """Return an empty registry."""
        return cls({})

    def resolve_topic(self, topic: str) -> tuple[str, int] | None:
        """Return ``(host, port)`` for the publisher of *topic*, or ``None``."""
        return self._topic_address.get(topic)

    @property
    def peers(self) -> dict[str, PeerEntry]:
        """All peers in the registry, keyed by name."""
        return self._peers
