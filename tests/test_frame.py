from h2r import frame


def test_encode_frame_prefixes_big_endian_length() -> None:
    assert frame.encode_frame(b"abc") == b"\x00\x00\x00\x03abc"


def test_encode_frame_empty_payload() -> None:
    assert frame.encode_frame(b"") == b"\x00\x00\x00\x00"


def test_decoder_single_frame_in_one_chunk() -> None:
    decoder = frame.FrameDecoder()
    assert decoder.feed(frame.encode_frame(b"hello")) == [b"hello"]


def test_decoder_multiple_frames_in_one_chunk() -> None:
    decoder = frame.FrameDecoder()
    chunk = frame.encode_frame(b"one") + frame.encode_frame(b"two") + frame.encode_frame(b"three")
    assert decoder.feed(chunk) == [b"one", b"two", b"three"]


def test_decoder_frame_split_across_chunks() -> None:
    decoder = frame.FrameDecoder()
    encoded = frame.encode_frame(b"split-me")
    assert decoder.feed(encoded[:3]) == []
    assert decoder.feed(encoded[3:6]) == []
    assert decoder.feed(encoded[6:]) == [b"split-me"]


def test_decoder_length_prefix_split_across_chunks() -> None:
    decoder = frame.FrameDecoder()
    encoded = frame.encode_frame(b"x")
    assert decoder.feed(encoded[:2]) == []
    assert decoder.feed(encoded[2:]) == [b"x"]


def test_decoder_holds_partial_frame_after_a_complete_one() -> None:
    decoder = frame.FrameDecoder()
    complete = frame.encode_frame(b"first")
    partial = frame.encode_frame(b"second")[:4]
    assert decoder.feed(complete + partial) == [b"first"]
    assert decoder.feed(frame.encode_frame(b"second")[4:]) == [b"second"]


def test_decoder_empty_payload_frame() -> None:
    decoder = frame.FrameDecoder()
    assert decoder.feed(frame.encode_frame(b"")) == [b""]


def test_decoder_no_frames_from_empty_feed() -> None:
    decoder = frame.FrameDecoder()
    assert decoder.feed(b"") == []


# `_extract_frames` is the pure core `FrameDecoder.feed` wraps; tested directly here
# because it needs no decoder instance, no chunking simulation, and no I/O to exercise
# every case exhaustively.


def test_extract_frames_empty_buffer() -> None:
    assert frame._extract_frames(b"") == ([], b"")


def test_extract_frames_returns_leftover_bytes_unchanged() -> None:
    leftover = frame.encode_frame(b"whole") + b"\x00\x00"
    frames, remainder = frame._extract_frames(leftover)
    assert frames == [b"whole"]
    assert remainder == b"\x00\x00"


def test_extract_frames_is_pure_does_not_mutate_input() -> None:
    buffer = frame.encode_frame(b"one") + frame.encode_frame(b"two")
    before = bytes(buffer)
    frame._extract_frames(buffer)
    assert buffer == before
