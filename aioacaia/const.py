"""Constants for aioacaia."""

from enum import StrEnum
from typing import Final

DEFAULT_CHAR_ID: Final = "49535343-8841-43f4-a8d4-ecbe34729bb3"
NOTIFY_CHAR_ID: Final = "49535343-1e4d-4bd9-ba61-23c647249616"
OLD_STYLE_CHAR_ID: Final = "00002a80-0000-1000-8000-00805f9b34fb"
HEADER1: Final = 0xEF
HEADER2: Final = 0xDD
HEARTBEAT_INTERVAL: Final = 5
SCALE_START_NAMES: Final = ["ACAIA", "PYXIS", "LUNAR", "PROCH"]


class UnitMass(StrEnum):
    """Unit of mass."""

    GRAMS = "grams"
    OUNCES = "ounces"


class Command(StrEnum):
    """Outgoing command message identifiers."""

    AUTH = "auth"
    TARE = "tare"
    START_TIMER = "startTimer"
    STOP_TIMER = "stopTimer"
    RESET_TIMER = "resetTimer"
    HEARTBEAT = "heartbeat"
    GET_SETTINGS = "getSettings"
    NOTIFICATION_REQUEST = "notificationRequest"
