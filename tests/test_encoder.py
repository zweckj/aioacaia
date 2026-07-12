"""Tests for aioacaia.encoder command framing."""

from aioacaia.encoder import encode, encode_id, encode_notification_request


def test_encode_frames_headers_type_and_split_checksum():
    """encode wraps the payload with headers, type and an even/odd checksum."""
    assert encode(5, [1, 2, 3, 4]) == bytes.fromhex("efdd05010203040406")


def test_encode_masks_payload_bytes_to_one_byte():
    """Payload values are masked to a single byte before framing."""
    assert encode(0, [0x102, 0x1]) == bytes.fromhex("efdd0002010201")


def test_encode_id_classic():
    """The classic identify payload is 15 dashes."""
    assert encode_id(is_pyxis_style=False) == bytes.fromhex(
        "efdd0b2d2d2d2d2d2d2d2d2d2d2d2d2d2d2d683b"
    )


def test_encode_id_pyxis():
    """The pyxis identify payload is the ascii digits 012345678901234."""
    assert encode_id(is_pyxis_style=True) == bytes.fromhex(
        "efdd0b3031323334353637383930313233349a6d"
    )


def test_encode_notification_request():
    """The notification request subscribes to the expected event registers."""
    assert encode_notification_request() == bytes.fromhex(
        "efdd0c0900010102020503041506"
    )
