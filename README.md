# h2r-py

[![codecov](https://codecov.io/gh/koichirokato/h2r-py/branch/main/graph/badge.svg)](https://codecov.io/gh/koichirokato/h2r-py)

h2r is a lightweight publish/subscribe middleware for robots, built on raw HTTP/2 streaming.

## Why

ROS 2 is powerful but heavy — it needs a DDS broker, pulls in many dependencies, and is
overkill for small or embedded robots. gRPC is lighter, but it's built around
request/response RPC, which doesn't fit continuous sensor data well.

h2r targets that gap: a broker-less middleware that streams data over plain HTTP/2 —
firewall-friendly, debuggable with standard tools (`curl --http2`, browser DevTools,
Wireshark), with no discovery daemon to run.

## Pub/sub model

Every topic is an HTTP/2 endpoint. The publisher *is* an HTTP/2 server; each subscriber
opens a single long-lived `GET` request and reads an infinite streaming response body —
one frame per message, no request/response round trip.

```
Subscriber                          Publisher
  GET /sensor/imu ──────────────►    :8081
                    ◄─────────────   200 OK (streaming body)
                    ◄─────────────   [frame 0]
                    ◄─────────────   [frame 1]
                    ...
```

Each frame is `[4-byte big-endian length][protobuf bytes]`. Peer discovery is static:
every node resolves topics to addresses from a shared YAML peer registry rather than
relying on multicast/mDNS.

This repository is a pure-Python implementation of h2r.

## Try it

`examples/publisher_example.py` advertises a topic and publishes an incrementing counter once a
second; `examples/subscriber_example.py` connects to it and prints what it receives. Run both in
the same dev container (they need to reach each other over the network):

```sh
docker compose run --rm dev bash
# inside the container:
uv run python examples/publisher_example.py &
uv run python examples/subscriber_example.py
```

Note: h2r uses broadcast semantics with no message replay (see "Pub/sub model" above), so the
subscriber only receives messages published after its connection completes. Because starting the
subscriber is itself racing against the publisher's one-second publish loop, its first printed
message is usually not "0" -- a few counts may already have been published (and dropped, for lack
of a connected subscriber) before it connects. This is expected, not a bug.

## Development

Docker + uv; no host Python setup required.

```sh
make build                # build the dev image
make sync                 # install dependencies
make check                # lint (ruff + ty) + test (pytest)
make pre-commit-install   # install the git pre-commit hook (ruff + ty on every commit)
```

Run `make help` for all targets.

## Layout

```
examples/    runnable publisher/subscriber sample
src/h2r/     package
tests/       pytest suite
```

## Status

Core protocol implemented: length-delimited framing (`h2r.frame`), the static peer registry
(`h2r.registry`), and the HTTP/2 publisher/subscriber (`h2r.publisher`, `h2r.subscriber`).
