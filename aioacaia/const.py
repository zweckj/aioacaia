"""Constants for aioacaia."""

from typing import Final
from enum import StrEnum

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


class AcaiaButton(StrEnum):
    """Acaia Button."""

    START = "start"
    STOP = "stop"
    RESET = "reset"
    TARE = "tare"
    UNKNOWN = "unknownbutton"
