"""Encode outgoing command messages for the scale."""

from collections.abc import Sequence
from enum import Enum
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


class Command(bytes, Enum):
    """A scale command whose value is its ready-to-send encoded payload."""

    TARE = encode(4, [0])
    START_TIMER = encode(13, [0, 0])
    STOP_TIMER = encode(13, [0, 2])
    RESET_TIMER = encode(13, [0, 1])
    HEARTBEAT = encode(0, [2, 0])
    GET_SETTINGS = encode(6, [0] * 16)
    NOTIFICATION_REQUEST = encode_notification_request()
    AUTH_CLASSIC = encode_id(is_pyxis_style=False)
    AUTH_PYXIS = encode_id(is_pyxis_style=True)
