"""Typed representations of scale notifications."""

from dataclasses import dataclass
from enum import IntEnum, StrEnum


class ButtonType(StrEnum):
    """Physical button reported by a button notification."""

    TARE = "tare"
    START = "start"
    STOP = "stop"
    RESET = "reset"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class WeightMessage:
    """A weight reading from the scale."""

    weight: float


@dataclass(frozen=True, slots=True)
class TimerMessage:
    """A timer reading from the scale."""

    time: float


@dataclass(frozen=True, slots=True)
class ButtonMessage:
    """A physical button press reported by the scale."""

    button: ButtonType
    timer_running: bool | None = None
    time: float | None = None
    weight: float | None = None


class AckResultType(IntEnum):
    """Category of the command an ack/heartbeat record refers to."""

    CMD = 0
    TIMER = 1
    UNIT = 2
    SLEEP = 3
    KEY_DISABLE = 4
    RESOLUTION = 5
    CAPABILITY = 6
    UNKNOWN = -1


class AckResultCode(IntEnum):
    """Outcome reported by an ack/heartbeat record."""

    WEIGHT_CMD_SUCCESS = 0
    BATTERY_CMD_SUCCESS = 1
    SET_PASSWORD_SUCCESS = 2
    SET_PASSWORD_FAIL = 3
    TARE_DONE = 4
    ISP_SUCCESS = 5
    ISP_FAIL = 6
    ALIVE_SUCCESS = 7


@dataclass(frozen=True, slots=True)
class AckMessage:
    """A bare command acknowledgement or keep-alive from the scale.

    Sent standalone as a heartbeat reply, or piggybacked inside a weight,
    timer or button record chain (in which case it is currently skipped
    rather than surfaced as its own message).
    """

    ack_id: int
    result_type: AckResultType
    result_value: AckResultCode


type ScaleMessage = WeightMessage | TimerMessage | ButtonMessage | AckMessage


@dataclass(frozen=True, slots=True)
class Settings:
    """Decoded settings from the scale."""

    battery: int
    units: str
    auto_off: int
    beep_on: bool
