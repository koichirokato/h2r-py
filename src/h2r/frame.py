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


def _extract_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Split *buffer* into complete frame payloads and the leftover partial frame.

    Pure: the result depends only on *buffer*, and nothing outside the return
    value is read or changed. :class:`FrameDecoder` is a thin stateful wrapper
    around this.
    """
    frames: list[bytes] = []
    offset = 0
    while len(buffer) - offset >= LENGTH_PREFIX_SIZE:
        (length,) = _LENGTH_PREFIX.unpack_from(buffer, offset)
        start = offset + LENGTH_PREFIX_SIZE
        end = start + length
        if len(buffer) < end:
            break
        frames.append(buffer[start:end])
        offset = end
    return frames, buffer[offset:]


class FrameDecoder:
    """Incrementally decodes length-delimited frames from a byte stream.

    HTTP/2 DATA chunks don't align with frame boundaries, so decoded frames
    are extracted as they complete and any trailing partial frame is held
    until :meth:`feed` receives its remainder.
    """

    def __init__(self) -> None:
        """Create a decoder with an empty internal buffer."""
        self._buffer = b""

    def feed(self, chunk: bytes) -> list[bytes]:
        """Append *chunk* and return any frame payloads it completed, in order."""
        frames, self._buffer = _extract_frames(self._buffer + chunk)
        return frames
