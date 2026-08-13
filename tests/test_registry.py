import pathlib

from h2r import registry

YAML_TEXT = """
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


def test_resolve_topic_returns_publisher_address() -> None:
    peer_registry = registry.PeerRegistry.from_string(YAML_TEXT)
    assert peer_registry.resolve_topic("/sensor/imu") == ("127.0.0.1", 8081)
    assert peer_registry.resolve_topic("/sensor/lidar") == ("127.0.0.1", 8081)


def test_resolve_topic_unknown_returns_none() -> None:
    peer_registry = registry.PeerRegistry.from_string(YAML_TEXT)
    assert peer_registry.resolve_topic("/no/such/topic") is None


def test_resolve_topic_does_not_resolve_subscribed_only_topics() -> None:
    peer_registry = registry.PeerRegistry.from_string(YAML_TEXT)
    assert peer_registry.resolve_topic("/cmd/velocity") is None


def test_empty_registry_resolves_nothing() -> None:
    peer_registry = registry.PeerRegistry.empty()
    assert peer_registry.resolve_topic("/sensor/imu") is None
    assert peer_registry.peers == {}


def test_from_string_with_no_peers_key() -> None:
    peer_registry = registry.PeerRegistry.from_string("{}")
    assert peer_registry.peers == {}


def test_peers_exposes_loaded_entries() -> None:
    peer_registry = registry.PeerRegistry.from_string(YAML_TEXT)
    assert set(peer_registry.peers) == {"sensor_node", "control_node"}
    assert peer_registry.peers["sensor_node"].publishes == ["/sensor/imu", "/sensor/lidar"]
    assert peer_registry.peers["control_node"].subscribes == ["/cmd/velocity"]


def test_from_file(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "peers.yaml"
    path.write_text(YAML_TEXT)
    peer_registry = registry.PeerRegistry.from_file(path)
    assert peer_registry.resolve_topic("/sensor/imu") == ("127.0.0.1", 8081)


# `_parse_peers` and `_build_topic_index` are the pure core `PeerRegistry` wraps; tested
# directly here since they need no YAML text, no registry instance, and no I/O.


def test_parse_peers_from_dict() -> None:
    peers = registry._parse_peers(
        {"peers": {"sensor_node": {"addr": "127.0.0.1:8081", "publishes": ["/sensor/imu"]}}},
    )
    assert peers == {
        "sensor_node": registry.PeerEntry(address="127.0.0.1:8081", publishes=["/sensor/imu"]),
    }


def test_parse_peers_none_data() -> None:
    assert registry._parse_peers(None) == {}


def test_parse_peers_empty_dict() -> None:
    assert registry._parse_peers({}) == {}


def test_build_topic_index_only_indexes_published_topics() -> None:
    peers = {
        "sensor_node": registry.PeerEntry(
            address="127.0.0.1:8081",
            publishes=["/sensor/imu", "/sensor/lidar"],
        ),
        "control_node": registry.PeerEntry(address="127.0.0.1:8082", subscribes=["/cmd/velocity"]),
    }
    assert registry._build_topic_index(peers) == {
        "/sensor/imu": ("127.0.0.1", 8081),
        "/sensor/lidar": ("127.0.0.1", 8081),
    }


def test_build_topic_index_empty_peers() -> None:
    assert registry._build_topic_index({}) == {}
