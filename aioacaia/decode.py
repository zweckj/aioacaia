"""Message decoding functions, taken from pyacaia."""

import logging
from dataclasses import dataclass

from bleak import BleakGATTCharacteristic

from .const import HEADER1, HEADER2
from .exceptions import AcaiaMessageError, AcaiaMessageTooLong, AcaiaMessageTooShort

_LOGGER = logging.getLogger(__name__)


@dataclass
class Message:
    """Representation of a message from the scale."""

    def __init__(self, msg_type: int, payload: bytearray | list[int]) -> None:
        self.msg_type = msg_type
        self.payload = payload
        self.value: float | None = None
        self.button: str | None = None
        self.time: int | None = None
        self.timer_running: bool | None = None

        _LOGGER.debug(
            "Message received: msg_type: %s, payload: %s",
            str(msg_type),
            payload,
        )

        # weight message
        if self.msg_type == 5:
            self.value = self._decode_weight(payload)

        # heartbeat response
        elif self.msg_type == 11:
            if payload[2] == 5:
                self.value = self._decode_weight(payload[3:])
            elif payload[2] == 7:
                self.time = self._decode_time(payload[3:])
            _LOGGER.debug(
                "heartbeat response (weight: %s, time: %s)", self.value, self.time
            )

        # time message
        elif self.msg_type == 7:
            self.time = self._decode_time(payload)
            _LOGGER.debug("timer: %s", self.time)

        # button message
        elif self.msg_type == 8:
            if payload[0] == 0 and payload[1] == 5:
                self.button = "tare"
                self.value = self._decode_weight(payload[2:])
                _LOGGER.debug("tare (weight: %s)", self.value)
            elif payload[0] == 8 and payload[1] == 5:
                self.button = "start"
                self.timer_running = True
                self.value = self._decode_weight(payload[2:])
                _LOGGER.debug("Timer started. Weight: %s", self.value)
            elif payload[0] == 8 and payload[1] == 11:
                self.button = "start"
                self.timer_running = True
                _LOGGER.debug("Timer started")
            elif payload[0] == 10 and payload[1] == 7:
                self.button = "stop"
                self.timer_running = False
                self.time = self._decode_time(payload[2:])
                self.value = self._decode_weight(payload[6:])
                _LOGGER.debug(
                    "Timer stopped. Time: %s, weight: %s",
                    self.time,
                    self.value,
                )
            elif payload[0] == 10 and payload[1] == 5:
                self.button = "stop"
                self.timer_running = False
                self.time = self._decode_time(payload[2:])
                _LOGGER.debug("Timer stopped. Time: %s", self.time)
            elif payload[0] == 10 and payload[1] == 13:
                self.button = "stop"
                self.timer_running = False
                _LOGGER.debug("Timer stopped")
            elif payload[0] == 9 and payload[1] == 7:
                self.button = "reset"
                self.time = self._decode_time(payload[2:])
                self.value = self._decode_weight(payload[6:])
                _LOGGER.debug(
                    "Timer reset. Time: %s, weight: %s", self.time, self.value
                )
            elif payload[0] == 9 and payload[1] == 5:
                self.button = "reset"
                self.time = self._decode_time(payload[2:])
                _LOGGER.debug("reset time: %s", self.time)

            elif payload[0] == 9 and payload[1] == 12:
                self.button = "reset"
                _LOGGER.debug("Timer reset")
            else:
                self.button = "unknownbutton"
                _LOGGER.debug(
                    "Uknown Button: %s,%s. Full payload: %s",
                    payload[0],
                    payload[1],
                    str(payload),
                )
        else:
            raise AcaiaMessageError(bytearray(payload), "Unknown message type")

    def _decode_weight(self, weight_payload):
        value = ((weight_payload[1] & 0xFF) << 8) + (weight_payload[0] & 0xFF)
        unit = weight_payload[4] & 0xFF
        if unit == 1:
            value /= 10.0
        elif unit == 2:
            value /= 100.0
        elif unit == 3:
            value /= 1000.0
        elif unit == 4:
            value /= 10000.0
        else:
            raise ValueError(f"unit value not in range {unit}")

        if (weight_payload[5] & 0x02) == 0x02:
            value *= -1
        return value

    def _decode_time(self, time_payload):
        value = (time_payload[0] & 0xFF) * 60
        value = value + (time_payload[1])
        value = value + (time_payload[2] / 10.0)
        return value


@dataclass
class Settings:
    """Representation of the settings from the scale."""

    def __init__(self, payload: bytearray) -> None:
        # payload[0] is unknown
        self.battery = payload[1] & 0x7F
        if payload[2] == 2:
            self.units = "grams"
        elif payload[2] == 5:
            self.units = "ounces"
        else:
            self.units = "grams"
        # payload[2 and 3] is unknown
        self.auto_off = payload[4] * 5
        # payload[5] is unknown
        self.beep_on = payload[6] == 1
        # payload[7-9] unknown
        _LOGGER.debug(
            "settings: battery=%s %s, auto_off=%s, beep=%s",
            self.battery,
            self.units,
            self.auto_off,
            self.beep_on,
        )
        _LOGGER.debug(
            "unknown settings: %s",
            str(
                [
                    payload[0],
                    payload[1] & 0x80,
                    payload[3],
                    payload[5],
                    payload[7],
                    payload[8],
                    payload[9],
                ]
            ),
        )


def decode(byte_msg: bytearray):
    """Return a tuple - first element is the message, or None
    if one not yet found.  Second is are the remaining
    bytes, which can be empty
    Messages are encoded as the encode() function above,
    min message length is 6 bytes
    HEADER1 (0xef)
    HEADER1 (0xdd)
    command
    length  (including this byte, excluding checksum)
    payload of length-1 bytes
    checksum byte1
    checksum byte2

    """
    msg_start = -1

    for i in range(len(byte_msg) - 1):
        if byte_msg[i] == HEADER1 and byte_msg[i + 1] == HEADER2:
            msg_start = i
            break

    if msg_start < 0 or len(byte_msg) - msg_start < 6:
        raise AcaiaMessageTooShort(byte_msg)

    msg_end = msg_start + byte_msg[msg_start + 3] + 5

    if msg_end > len(byte_msg):
        if byte_msg[i] != HEADER1 or byte_msg[1] != HEADER2:
            raise AcaiaMessageError(byte_msg, "Long message without headers")
        raise AcaiaMessageTooLong(byte_msg)

    if msg_start > 0:
        _LOGGER.debug("Ignoring %s bytes before header", i)

    cmd = byte_msg[msg_start + 2]
    if cmd == 12:
        msg_type = byte_msg[msg_start + 4]
        payload_in = byte_msg[msg_start + 5 : msg_end]
        return (Message(msg_type, payload_in), byte_msg[msg_end:])
    if cmd == 8:
        return (Settings(byte_msg[msg_start + 3 :]), byte_msg[msg_end:])

    _LOGGER.debug(
        "Non event notification message command %s %s",
        str(cmd),
        str(byte_msg[msg_start:msg_end]),
    )
    _LOGGER.debug("Full message: %s", byte_msg)
    return (None, byte_msg[msg_end:])


def notification_handler(sender: BleakGATTCharacteristic, data: bytearray) -> None:
    """Sample for callback for handling incoming notifications from the scale."""
    msg = decode(data)[0]
    if isinstance(msg, Settings):
        print(f"Battery: {msg.battery}")
        print(f"Units: {msg.units}")
    elif isinstance(msg, Message):
        print(f"Weight: {msg.value}")
