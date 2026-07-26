"""Decode raw scale notification bytes into messages."""

import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import Final

from bleak.backends.characteristic import BleakGATTCharacteristic

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

# Record tags that carry no message of their own.
_BATTERY_TAG: Final = 6
_UNKNOWN_TAG_0B: Final = 11

# Divisor applied to a raw weight value, keyed by the unit byte.
_WEIGHT_UNIT_DIVISORS: Final = {1: 10.0, 2: 100.0, 3: 1000.0, 4: 10000.0}


class MessageType(IntEnum):
    """Type of an event notification payload."""

    WEIGHT = 5
    TIMER = 7
    BUTTON = 8
    HEARTBEAT = 11


def _require_payload_length(
    payload: bytearray | list[int], minimum: int, message_name: str
) -> None:
    """Reject payloads that cannot contain the requested message."""
    if len(payload) < minimum:
        raise AcaiaMessageError(
            bytearray(payload), f"{message_name} payload is too short"
        )


def decode_weight(weight_payload: bytearray | list[int]) -> float:
    """Decode a weight in grams from a payload."""
    _require_payload_length(weight_payload, 6, "Weight")
    value: float = ((weight_payload[1] & 0xFF) << 8) + (weight_payload[0] & 0xFF)
    unit = weight_payload[4] & 0xFF
    if unit not in _WEIGHT_UNIT_DIVISORS:
        raise AcaiaMessageError(
            bytearray(weight_payload), f"Unknown weight unit {unit}"
        )
    value /= _WEIGHT_UNIT_DIVISORS[unit]
    if weight_payload[5] & 0x02:
        value *= -1
    return value


def decode_time(time_payload: bytearray | list[int]) -> float:
    """Decode a time in seconds from a payload."""
    _require_payload_length(time_payload, 3, "Timer")
    minutes = (time_payload[0] & 0xFF) * 60
    return minutes + time_payload[1] + time_payload[2] / 10.0


# Record tags and the fixed width of each record's body. Everything the scale
# reports in an event payload is a chain of [tag][body] records running to the
# end of the frame, so the same walk decodes a button's attached fields and a
# heartbeat's wrapped payload. Tag 0x0b is unexplained (a constant 00 e0) but
# its width has to be right or the rest of the chain is lost behind it.
_RECORD_WIDTHS: Final[dict[int, int]] = {
    MessageType.WEIGHT: 6,
    _BATTERY_TAG: 1,
    MessageType.TIMER: 3,
    _UNKNOWN_TAG_0B: 2,
}

# Buttons keyed by key code alone; what follows is the ordinary record chain.
_BUTTON_TYPES: Final[dict[int, tuple[ButtonType, bool | None]]] = {
    0: (ButtonType.TARE, None),
    8: (ButtonType.START, True),
    9: (ButtonType.RESET, None),
    10: (ButtonType.STOP, False),
}


@dataclass(frozen=True, slots=True)
class _Records:
    """Fields collected from one record chain."""

    key: int | None = None
    weight: float | None = None
    time: float | None = None


def _walk_records(payload: bytearray | list[int]) -> _Records:
    """Walk a [tag][body] chain, collecting the fields it carries.

    An unrecognised tag makes everything behind it unreadable, so the walk
    stops there rather than guessing at offsets. A known tag whose body is cut
    short by the end of the frame is a fragment, not vocabulary — also stop.
    """
    key = weight = time = None
    index = 0
    while index < len(payload):
        tag = payload[index]
        if tag == MessageType.BUTTON:
            if index + 2 > len(payload):
                break
            key = payload[index + 1]
            index += 2
            continue
        width = _RECORD_WIDTHS.get(tag)
        if width is None:
            _LOGGER.debug("Unknown record tag %s in payload: %s", tag, payload)
            break
        body = payload[index + 1 : index + 1 + width]
        if len(body) < width:
            break
        if tag == MessageType.WEIGHT:
            weight = decode_weight(body)
        elif tag == MessageType.TIMER:
            time = decode_time(body)
        index += 1 + width
    return _Records(key, weight, time)


def _parse_button(payload: bytearray | list[int]) -> ButtonMessage:
    """Decode a button notification payload."""
    _require_payload_length(payload, 1, "Button")
    records = _walk_records([MessageType.BUTTON, *payload])
    if records.key not in _BUTTON_TYPES:
        _LOGGER.debug("Unknown button, full payload: %s", payload)
        return ButtonMessage(ButtonType.UNKNOWN)
    button, timer_running = _BUTTON_TYPES[records.key]
    return ButtonMessage(button, timer_running, records.time, records.weight)


def _parse_heartbeat(
    payload: bytearray | list[int],
) -> ScaleMessage | None:
    """Decode whatever a heartbeat response wraps.

    The wrapper is simply the first record, identified by its tag at
    ``payload[2]``: a nested button — which the scale does send — decodes as a
    press, otherwise the wrapped weight or timer is decoded directly.
    """
    _require_payload_length(payload, 3, "Heartbeat")
    inner_tag = payload[2]
    if inner_tag == MessageType.BUTTON:
        return _parse_button(payload[3:])
    if inner_tag == MessageType.WEIGHT:
        return WeightMessage(decode_weight(payload[3:]))
    if inner_tag == MessageType.TIMER:
        return TimerMessage(decode_time(payload[3:]))
    return None


def _parse_settings(payload: bytearray) -> Settings:
    """Decode a settings payload."""
    _require_payload_length(payload, 7, "Settings")
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
    match msg_type:
        case MessageType.WEIGHT:
            return WeightMessage(decode_weight(payload))
        case MessageType.TIMER:
            return TimerMessage(decode_time(payload))
        case MessageType.HEARTBEAT:
            return _parse_heartbeat(payload)
        case MessageType.BUTTON:
            return _parse_button(payload)
        case _:
            raise AcaiaMessageError(bytearray(payload), "Unknown message type")


def _validate_checksum(byte_msg: bytearray, start: int, msg_end: int) -> None:
    """Validate the even and odd checksums at the end of a frame."""
    body = byte_msg[start + _LENGTH_OFFSET : msg_end - 2]
    expected_even = sum(body[0::2]) & 0xFF
    expected_odd = sum(body[1::2]) & 0xFF
    if byte_msg[msg_end - 2 : msg_end] != bytearray((expected_even, expected_odd)):
        raise AcaiaMessageError(
            byte_msg[start:msg_end], "Message checksum does not match"
        )


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

    _validate_checksum(byte_msg, start, msg_end)

    command = byte_msg[start + _COMMAND_OFFSET]
    remaining = byte_msg[msg_end:]

    if command == _EVENT_COMMAND:
        if byte_msg[start + _LENGTH_OFFSET] < 2:
            raise AcaiaMessageError(
                byte_msg[start:msg_end], "Event payload is too short"
            )
        msg_type = byte_msg[start + _MESSAGE_TYPE_OFFSET]
        payload = byte_msg[start + _PAYLOAD_OFFSET : msg_end - 2]
        return _parse_message(msg_type, payload), remaining

    if command == _SETTINGS_COMMAND:
        return _parse_settings(
            byte_msg[start + _LENGTH_OFFSET : msg_end - 2]
        ), remaining

    _LOGGER.debug(
        "Non event notification message command %s: %s",
        command,
        byte_msg[start:msg_end],
    )
    return None, remaining


def notification_handler(_: BleakGATTCharacteristic, data: bytearray) -> None:
    """Sample for callback for handling incoming notifications from the scale."""
    match decode(data)[0]:
        case Settings(battery=battery, units=units):
            _LOGGER.info("Battery: %s", battery)
            _LOGGER.info("Units: %s", units)
        case WeightMessage(weight=weight):
            _LOGGER.info("Weight: %s", weight)
