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


class PeerRegistry:
    """Loaded peer registry.

    Provides topic -> ``(host, port)`` resolution for the subscriber side.
    """

    def __init__(self, peers: dict[str, PeerEntry]) -> None:
        """Index *peers* by the topics they publish."""
        self._peers = peers
        self._topic_address: dict[str, tuple[str, int]] = {}
        for entry in peers.values():
            host, _, port_str = entry.address.rpartition(":")
            port = int(port_str)
            for topic in entry.publishes:
                self._topic_address[topic] = (host, port)

    @classmethod
    def from_file(cls, path: str | pathlib.Path) -> PeerRegistry:
        """Load the registry from a YAML file."""
        with pathlib.Path(path).open() as file:
            data = yaml.safe_load(file)
        return cls._from_dict(data)

    @classmethod
    def from_string(cls, yaml_text: str) -> PeerRegistry:
        """Parse the registry from a YAML string."""
        data = yaml.safe_load(yaml_text)
        return cls._from_dict(data)

    @classmethod
    def empty(cls) -> PeerRegistry:
        """Return an empty registry."""
        return cls({})

    @classmethod
    def _from_dict(cls, data: dict[str, Any] | None) -> PeerRegistry:
        peers: dict[str, PeerEntry] = {}
        for name, raw in ((data or {}).get("peers") or {}).items():
            peers[name] = PeerEntry(
                address=raw["addr"],
                publishes=raw.get("publishes") or [],
                subscribes=raw.get("subscribes") or [],
            )
        return cls(peers)

    def resolve_topic(self, topic: str) -> tuple[str, int] | None:
        """Return ``(host, port)`` for the publisher of *topic*, or ``None``."""
        return self._topic_address.get(topic)

    @property
    def peers(self) -> dict[str, PeerEntry]:
        """All peers in the registry, keyed by name."""
        return self._peers
