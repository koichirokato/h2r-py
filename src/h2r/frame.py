"""Length-delimited framing: ``[4-byte big-endian length][payload]``.

Matches h2r-core's wire format (and gRPC's), so a frame boundary never has to
guess where a message ends inside an HTTP/2 DATA chunk.
"""

from __future__ import annotations

import struct

_LENGTH_PREFIX = struct.Struct(">I")
LENGTH_PREFIX_SIZE = _LENGTH_PREFIX.size


def encode_frame(payload: bytes) -> bytes:
    """Wrap *payload* in a length-delimited frame."""
    return _LENGTH_PREFIX.pack(len(payload)) + payload


class FrameDecoder:
    """Incrementally decodes length-delimited frames from a byte stream.

    HTTP/2 DATA chunks don't align with frame boundaries, so decoded frames
    are extracted as they complete and any trailing partial frame is held
    until :meth:`feed` receives its remainder.
    """

    def __init__(self) -> None:
        """Create a decoder with an empty internal buffer."""
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        """Append *chunk* and return any frame payloads it completed, in order."""
        self._buffer.extend(chunk)
        frames: list[bytes] = []
        while len(self._buffer) >= LENGTH_PREFIX_SIZE:
            (length,) = _LENGTH_PREFIX.unpack_from(self._buffer)
            end = LENGTH_PREFIX_SIZE + length
            if len(self._buffer) < end:
                break
            frames.append(bytes(self._buffer[LENGTH_PREFIX_SIZE:end]))
            del self._buffer[:end]
        return frames
