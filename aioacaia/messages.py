"""Typed representations of scale notifications."""

from dataclasses import dataclass
from enum import StrEnum


class ButtonType(StrEnum):
    """Physical button reported by a button notification."""

    TARE = "tare"
    START = "start"
    STOP = "stop"
    RESET = "reset"
    UNKNOWN = "unknownbutton"


@dataclass(frozen=True)
class WeightMessage:
    """A weight reading from the scale."""

    weight: float


@dataclass(frozen=True)
class TimerMessage:
    """A timer reading from the scale."""

    time: float


@dataclass(frozen=True)
class ButtonMessage:
    """A physical button press reported by the scale."""

    button: ButtonType
    timer_running: bool | None = None
    time: float | None = None
    weight: float | None = None


ScaleMessage = WeightMessage | TimerMessage | ButtonMessage


@dataclass(frozen=True)
class Settings:
    """Decoded settings from the scale."""

    battery: int
    units: str
    auto_off: int
    beep_on: bool
