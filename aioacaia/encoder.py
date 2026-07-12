"""Encode outgoing command messages for the scale."""

from collections.abc import Sequence
from typing import Final

from .const import HEADER1, HEADER2

# Identify payloads expected by the two scale generations (ascii bytes).
_CLASSIC_ID: Final = b"-" * 15
_PYXIS_ID: Final = b"012345678901234"


def encode(msg_type: int, payload: Sequence[int]) -> bytes:
    """Frame a command for the scale with headers, type, payload and checksum."""
    body = bytes(byte & 0xFF for byte in payload)
    even = sum(body[0::2]) & 0xFF
    odd = sum(body[1::2]) & 0xFF
    return bytes((HEADER1, HEADER2, msg_type, *body, even, odd))


def encode_id(is_pyxis_style: bool = False) -> bytes:
    """Encode the identify/auth message for the scale."""
    return encode(11, _PYXIS_ID if is_pyxis_style else _CLASSIC_ID)


def encode_notification_request() -> bytes:
    """Encode the request subscribing to weight, battery, timer and key events."""
    # fmt: off
    register = (
        0, 1,  # weight
        1, 2,  # battery
        2, 5,  # timer (number of heartbeats between timer messages)
        3, 4,  # key / settings
    )
    # fmt: on
    payload = [len(register) + 1, *register]
    return encode(12, payload)
