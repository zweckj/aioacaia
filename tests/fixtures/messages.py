"""Real and crafted scale messages used by the decode tests."""

from aioacaia.const import HEADER1, HEADER2

# Command byte for event notifications (weight/timer/button/heartbeat).
_EVENT_CMD = 0x0C


def _frame(command: int, payload: bytes) -> bytes:
    """Wrap a command payload with its length and split checksum."""
    body = bytes([len(payload) + 1]) + payload
    checksum = bytes((sum(body[0::2]) & 0xFF, sum(body[1::2]) & 0xFF))
    return bytes([HEADER1, HEADER2, command]) + body + checksum


def frame(msg_type: int, payload: bytes) -> bytes:
    """Wrap a payload in an event-notification frame (command 12)."""
    return _frame(_EVENT_CMD, bytes([msg_type]) + payload)


# Reusable sub-payloads with known decoded values.
_WEIGHT = bytes([0xDF, 0x06, 0x00, 0x00, 0x01, 0x00])  # -> 175.9 g (unit 1, positive)
_TIME = bytes([0x01, 0x1E, 0x05])  # -> 90.5 s (1 min, 30 s, .5)

# --- Weight (msg_type 5) ---
# Real capture from a scale; decodes to 175.9 g.
WEIGHT = bytes.fromhex("efdd0c0c05df060000010007000002f30d")
WEIGHT_NEGATIVE = frame(5, bytes([0xDF, 0x06, 0x00, 0x00, 0x01, 0x02]))  # -> -175.9
WEIGHT_UNIT_100 = frame(5, bytes([0xDF, 0x06, 0x00, 0x00, 0x02, 0x00]))  # -> 17.59
WEIGHT_UNIT_1000 = frame(5, bytes([0xDF, 0x06, 0x00, 0x00, 0x03, 0x00]))  # -> 1.759
WEIGHT_UNIT_10000 = frame(5, bytes([0xDF, 0x06, 0x00, 0x00, 0x04, 0x00]))  # -> 0.1759
WEIGHT_BAD_UNIT = frame(
    5, bytes([0xDF, 0x06, 0x00, 0x00, 0x00, 0x00])
)  # unit 0 -> ValueError

# --- Timer (msg_type 7) ---
TIMER = frame(7, _TIME)  # -> 90.5

# --- Heartbeat (msg_type 11) wrapping a weight or timer payload ---
HEARTBEAT_WEIGHT = frame(11, bytes([0x00, 0x00, 0x05]) + _WEIGHT)  # value 175.9
HEARTBEAT_TIME = frame(11, bytes([0x00, 0x00, 0x07]) + _TIME)  # time 90.5

# --- Button (msg_type 8) ---
# A press is a key code followed by the ordinary record chain, so the byte
# after the 3 time bytes is not padding — it is the tag introducing the weight
# record that follows.
_TIME_FIELD = _TIME + bytes([0x05])
BUTTON_TARE = frame(8, bytes([0, 5]) + _WEIGHT)
BUTTON_START_WEIGHT = frame(8, bytes([8, 5]) + _WEIGHT)
BUTTON_START = frame(8, bytes([8, 11]))
BUTTON_STOP_TIME_WEIGHT = frame(8, bytes([10, 7]) + _TIME_FIELD + _WEIGHT)
BUTTON_STOP_TIME = frame(8, bytes([10, 7]) + _TIME)
BUTTON_STOP = frame(8, bytes([10, 13]))
BUTTON_RESET_TIME_WEIGHT = frame(8, bytes([9, 7]) + _TIME_FIELD + _WEIGHT)
BUTTON_RESET_TIME = frame(8, bytes([9, 7]) + _TIME)
BUTTON_RESET = frame(8, bytes([9, 12]))
BUTTON_STOP_WEIGHT = frame(8, bytes([10, 5]) + _WEIGHT)
BUTTON_RESET_WEIGHT = frame(8, bytes([9, 5]) + _WEIGHT)
BUTTON_UNKNOWN = frame(8, bytes([7, 7]))

# Verbatim captures from a PEARL-244684 (original Pearl). Elapsed times were
# cross-checked against wall clock, so these are ground truth rather than
# frames built to match the parser.
REAL_STOP_WEIGHT_TIME = frame(  # 55.1 g, 9.7 s
    8, bytes.fromhex("0a05270200000101 07000907".replace(" ", ""))
)
REAL_START_BEHIND_TAG_0B = frame(  # 54.9 g, behind a 0b record
    8, bytes.fromhex("080b00e005250200000101")
)
REAL_RESET_BEHIND_TAG_0B = frame(  # 0 g, behind a 0b record
    8, bytes.fromhex("090b00e005000000000101")
)
REAL_HEARTBEAT_WRAPPED_START = frame(  # a start press nested in a heartbeat
    11, bytes.fromhex("00e00808050000000001 01".replace(" ", ""))
)

# --- Unknown msg_type ---
UNKNOWN_TYPE = frame(99, bytes([0, 0]))

# --- Settings (command 8) ---
# Real capture; battery 93, grams, auto_off 5 min, beep on.
SETTINGS_GRAMS = bytes.fromhex("efdd08095d020201000101000d60")
# Same message with the units byte flipped to ounces (0x05).
SETTINGS_OUNCES = _frame(0x08, bytes.fromhex("5d05020100010100"))

# --- decode() framing edge cases ---
HEADER_ONLY = bytes.fromhex("efdd0c")  # too short; also split part 1
SPLIT_REMAINDER = bytes.fromhex("0c05df060000010007000002f30d")  # split part 2
TOO_LONG = bytes.fromhex("efdd0c200500")  # length byte claims more bytes than present
UNKNOWN_COMMAND = _frame(0x05, b"\x00")
LEADING_GARBAGE = bytes([0x00, 0x01, 0x02]) + WEIGHT
TWO_MESSAGES = WEIGHT + SETTINGS_GRAMS
BAD_CHECKSUM = WEIGHT[:-1] + bytes([WEIGHT[-1] ^ 0x01])
SHORT_WEIGHT = frame(5, b"\x00")
WEIGHT_WITH_EMBEDDED_HEADER = frame(
    5, bytes([HEADER1, HEADER2, 0x00, 0x00, 0x01, 0x00])
)
