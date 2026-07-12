"""Decode raw scale notification bytes into messages."""

import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import Final

from bleak import BleakGATTCharacteristic

from .const import HEADER1, HEADER2
from .exceptions import AcaiaMessageError, AcaiaMessageTooLong, AcaiaMessageTooShort
from .messages import (
    ButtonMessage,
    ButtonType,
    ScaleMessage,
    Settings,
    TimerMessage,
    WeightMessage,
)

_LOGGER = logging.getLogger(__name__)

_EVENT_COMMAND: Final = 12
_SETTINGS_COMMAND: Final = 8

# Two-byte start-of-frame marker.
_HEADER: Final = bytes([HEADER1, HEADER2])
_MIN_FRAME_LENGTH: Final = 6
# Byte offsets within a frame, relative to the start of the header.
_COMMAND_OFFSET: Final = 2
_LENGTH_OFFSET: Final = 3
_MESSAGE_TYPE_OFFSET: Final = 4
_PAYLOAD_OFFSET: Final = 5
# Non-payload bytes not counted by the length byte: 2 headers + command + 2 checksum.
_FRAME_OVERHEAD: Final = 5

# Divisor applied to a raw weight value, keyed by the unit byte.
_WEIGHT_UNIT_DIVISORS: Final = {1: 10.0, 2: 100.0, 3: 1000.0, 4: 10000.0}


class MessageType(IntEnum):
    """Type of an event notification payload."""

    WEIGHT = 5
    TIMER = 7
    BUTTON = 8
    HEARTBEAT = 11


def decode_weight(weight_payload: bytearray | list[int]) -> float:
    """Decode a weight in grams from a payload."""
    value: float = ((weight_payload[1] & 0xFF) << 8) + (weight_payload[0] & 0xFF)
    unit = weight_payload[4] & 0xFF
    if unit not in _WEIGHT_UNIT_DIVISORS:
        raise ValueError(f"unit value not in range {unit}")
    value /= _WEIGHT_UNIT_DIVISORS[unit]
    if weight_payload[5] & 0x02:
        value *= -1
    return value


def decode_time(time_payload: bytearray | list[int]) -> float:
    """Decode a time in seconds from a payload."""
    minutes = (time_payload[0] & 0xFF) * 60
    return minutes + time_payload[1] + time_payload[2] / 10.0


@dataclass(frozen=True)
class _ButtonEvent:
    """How to decode a button notification payload."""

    button: ButtonType
    timer_running: bool | None = None
    time_at: int | None = None
    weight_at: int | None = None


# Button events keyed by (payload[0], payload[1]).
_BUTTON_EVENTS: Final[dict[tuple[int, int], _ButtonEvent]] = {
    (0, 5): _ButtonEvent(ButtonType.TARE, weight_at=2),
    (8, 5): _ButtonEvent(ButtonType.START, timer_running=True, weight_at=2),
    (8, 11): _ButtonEvent(ButtonType.START, timer_running=True),
    (10, 7): _ButtonEvent(ButtonType.STOP, timer_running=False, time_at=2, weight_at=6),
    (10, 5): _ButtonEvent(ButtonType.STOP, timer_running=False, time_at=2),
    (10, 13): _ButtonEvent(ButtonType.STOP, timer_running=False),
    (9, 7): _ButtonEvent(ButtonType.RESET, time_at=2, weight_at=6),
    (9, 5): _ButtonEvent(ButtonType.RESET, time_at=2),
    (9, 12): _ButtonEvent(ButtonType.RESET),
}


def _parse_button(payload: bytearray | list[int]) -> ButtonMessage:
    """Decode a button notification payload."""
    event = _BUTTON_EVENTS.get((payload[0], payload[1]))
    if event is None:
        _LOGGER.debug("Unknown button, full payload: %s", payload)
        return ButtonMessage(ButtonType.UNKNOWN)

    time = decode_time(payload[event.time_at :]) if event.time_at is not None else None
    weight = (
        decode_weight(payload[event.weight_at :])
        if event.weight_at is not None
        else None
    )
    return ButtonMessage(event.button, event.timer_running, time, weight)


def _parse_heartbeat(
    payload: bytearray | list[int],
) -> WeightMessage | TimerMessage | None:
    """Decode the weight or timer wrapped in a heartbeat response."""
    inner_type = payload[2]
    if inner_type == MessageType.WEIGHT:
        return WeightMessage(decode_weight(payload[3:]))
    if inner_type == MessageType.TIMER:
        return TimerMessage(decode_time(payload[3:]))
    return None


def _parse_settings(payload: bytearray) -> Settings:
    """Decode a settings payload."""
    settings = Settings(
        battery=payload[1] & 0x7F,
        units="ounces" if payload[2] == 5 else "grams",
        auto_off=payload[4] * 5,
        beep_on=payload[6] == 1,
    )
    _LOGGER.debug(
        "settings: battery=%s %s, auto_off=%s, beep=%s",
        settings.battery,
        settings.units,
        settings.auto_off,
        settings.beep_on,
    )
    return settings


def _parse_message(
    msg_type: int, payload: bytearray | list[int]
) -> ScaleMessage | None:
    """Decode an event notification payload into a message."""
    _LOGGER.debug("Message received: msg_type: %s, payload: %s", msg_type, payload)
    if msg_type == MessageType.WEIGHT:
        return WeightMessage(decode_weight(payload))
    if msg_type == MessageType.TIMER:
        return TimerMessage(decode_time(payload))
    if msg_type == MessageType.HEARTBEAT:
        return _parse_heartbeat(payload)
    if msg_type == MessageType.BUTTON:
        return _parse_button(payload)
    raise AcaiaMessageError(bytearray(payload), "Unknown message type")


def decode(byte_msg: bytearray) -> tuple[ScaleMessage | Settings | None, bytearray]:
    """Decode one message, returning it (or None) and any remaining bytes."""
    # Frame layout: HEADER1, HEADER2, command, length, payload..., checksum, checksum
    start = byte_msg.find(_HEADER)
    if start < 0 or len(byte_msg) - start < _MIN_FRAME_LENGTH:
        raise AcaiaMessageTooShort(byte_msg)

    msg_end = start + byte_msg[start + _LENGTH_OFFSET] + _FRAME_OVERHEAD
    if msg_end > len(byte_msg):
        raise AcaiaMessageTooLong(byte_msg)

    if start > 0:
        _LOGGER.debug("Ignoring %s bytes before header", start)

    command = byte_msg[start + _COMMAND_OFFSET]
    remaining = byte_msg[msg_end:]

    if command == _EVENT_COMMAND:
        msg_type = byte_msg[start + _MESSAGE_TYPE_OFFSET]
        payload = byte_msg[start + _PAYLOAD_OFFSET : msg_end]
        return _parse_message(msg_type, payload), remaining

    if command == _SETTINGS_COMMAND:
        return _parse_settings(byte_msg[start + _LENGTH_OFFSET : msg_end]), remaining

    _LOGGER.debug(
        "Non event notification message command %s: %s",
        command,
        byte_msg[start:msg_end],
    )
    return None, remaining


def notification_handler(_: BleakGATTCharacteristic, data: bytearray) -> None:
    """Sample for callback for handling incoming notifications from the scale."""
    msg = decode(data)[0]
    if isinstance(msg, Settings):
        print(f"Battery: {msg.battery}")
        print(f"Units: {msg.units}")
    elif isinstance(msg, WeightMessage):
        print(f"Weight: {msg.weight}")
