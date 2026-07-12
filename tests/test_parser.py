"""Tests for aioacaia.parser covering each message-parsing path."""

import pytest

from aioacaia.scale import AcaiaScale
from aioacaia.messages import (
    ButtonMessage,
    ButtonType,
    Settings,
    TimerMessage,
    WeightMessage,
)
from aioacaia.parser import decode
from aioacaia.exceptions import (
    AcaiaMessageError,
    AcaiaMessageTooLong,
    AcaiaMessageTooShort,
)
from tests.fixtures import messages as m


def _decode_message(raw: bytes):
    """Decode a single, complete message and assert nothing remains."""
    msg, remaining = decode(bytearray(raw))
    assert remaining == b""
    return msg


def _assert_close(actual, expected):
    """Assert equality, tolerating float rounding, and handling None."""
    if expected is None:
        assert actual is None
    else:
        assert actual == pytest.approx(expected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (m.WEIGHT, 175.9),
        (m.WEIGHT_NEGATIVE, -175.9),
        (m.WEIGHT_UNIT_100, 17.59),
        (m.WEIGHT_UNIT_1000, 1.759),
        (m.WEIGHT_UNIT_10000, 0.1759),
    ],
)
def test_decode_weight(raw, expected):
    """Weight messages decode to the expected gram value."""
    msg = _decode_message(raw)
    assert isinstance(msg, WeightMessage)
    assert msg.weight == pytest.approx(expected)


def test_decode_weight_invalid_unit_raises():
    """An out-of-range unit byte raises ValueError."""
    with pytest.raises(ValueError):
        decode(bytearray(m.WEIGHT_BAD_UNIT))


def test_decode_timer():
    """Timer messages decode to seconds."""
    msg = _decode_message(m.TIMER)
    assert isinstance(msg, TimerMessage)
    assert msg.time == pytest.approx(90.5)


def test_decode_heartbeat_weight():
    """A heartbeat wrapping a weight decodes to a WeightMessage."""
    msg = _decode_message(m.HEARTBEAT_WEIGHT)
    assert isinstance(msg, WeightMessage)
    assert msg.weight == pytest.approx(175.9)


def test_decode_heartbeat_time():
    """A heartbeat wrapping a timer decodes to a TimerMessage."""
    msg = _decode_message(m.HEARTBEAT_TIME)
    assert isinstance(msg, TimerMessage)
    assert msg.time == pytest.approx(90.5)


@pytest.mark.parametrize(
    ("raw", "button", "timer_running", "expected_time", "expected_weight"),
    [
        (m.BUTTON_TARE, ButtonType.TARE, None, None, 175.9),
        (m.BUTTON_START_WEIGHT, ButtonType.START, True, None, 175.9),
        (m.BUTTON_START, ButtonType.START, True, None, None),
        (m.BUTTON_STOP_TIME_WEIGHT, ButtonType.STOP, False, 90.5, 175.9),
        (m.BUTTON_STOP_TIME, ButtonType.STOP, False, 90.5, None),
        (m.BUTTON_STOP, ButtonType.STOP, False, None, None),
        (m.BUTTON_RESET_TIME_WEIGHT, ButtonType.RESET, None, 90.5, 175.9),
        (m.BUTTON_RESET_TIME, ButtonType.RESET, None, 90.5, None),
        (m.BUTTON_RESET, ButtonType.RESET, None, None, None),
        (m.BUTTON_UNKNOWN, ButtonType.UNKNOWN, None, None, None),
    ],
)
def test_decode_button(raw, button, timer_running, expected_time, expected_weight):
    """Button messages decode to the right event, timer state, time and weight."""
    msg = _decode_message(raw)
    assert isinstance(msg, ButtonMessage)
    assert msg.button is button
    assert msg.timer_running is timer_running
    _assert_close(msg.time, expected_time)
    _assert_close(msg.weight, expected_weight)


def test_decode_unknown_type_raises():
    """An unknown message type raises AcaiaMessageError."""
    with pytest.raises(AcaiaMessageError):
        decode(bytearray(m.UNKNOWN_TYPE))


@pytest.mark.parametrize(
    ("raw", "units"),
    [
        (m.SETTINGS_GRAMS, "grams"),
        (m.SETTINGS_OUNCES, "ounces"),
    ],
)
def test_decode_settings(raw, units):
    """Settings messages decode battery, units, auto-off and beep."""
    settings, remaining = decode(bytearray(raw))
    assert isinstance(settings, Settings)
    assert remaining == b""
    assert settings.units == units
    assert settings.battery == 93
    assert settings.auto_off == 5
    assert settings.beep_on is True


def test_decode_too_short_raises():
    """A header-only message raises AcaiaMessageTooShort with the received bytes."""
    with pytest.raises(AcaiaMessageTooShort) as exc:
        decode(bytearray(m.HEADER_ONLY))
    assert exc.value.bytes_recvd == bytearray(m.HEADER_ONLY)


def test_decode_too_long_raises():
    """A length byte past the buffer raises AcaiaMessageTooLong."""
    with pytest.raises(AcaiaMessageTooLong):
        decode(bytearray(m.TOO_LONG))


def test_decode_unknown_command_returns_none():
    """An unknown command yields no message but consumes the bytes."""
    msg, remaining = decode(bytearray(m.UNKNOWN_COMMAND))
    assert msg is None
    assert remaining == b""


def test_decode_skips_leading_garbage():
    """Bytes before the header are ignored."""
    msg = _decode_message(m.LEADING_GARBAGE)
    assert isinstance(msg, WeightMessage)
    assert msg.weight == pytest.approx(175.9)


def test_decode_returns_remaining_bytes():
    """Trailing bytes after a message are returned for the next decode."""
    first, remaining = decode(bytearray(m.TWO_MESSAGES))
    assert isinstance(first, WeightMessage)
    assert remaining == bytearray(m.SETTINGS_GRAMS)
    second, tail = decode(remaining)
    assert isinstance(second, Settings)
    assert tail == b""


def test_decode_split_message_reassembly():
    """A message split across two chunks decodes once reassembled."""
    with pytest.raises(AcaiaMessageTooShort) as exc:
        decode(bytearray(m.HEADER_ONLY))
    assert exc.value.bytes_recvd == bytearray(m.HEADER_ONLY)

    msg, remaining = decode(bytearray(m.HEADER_ONLY + m.SPLIT_REMAINDER))
    assert isinstance(msg, WeightMessage)
    assert msg.weight == pytest.approx(175.9)
    assert remaining == b""


async def test_scale_reassembles_split_notifications():
    """The scale buffers a header-only chunk and decodes it with the next."""
    scale = AcaiaScale("aa:bb:cc:dd:ee:ff")

    await scale.on_bluetooth_data_received(None, bytearray(m.HEADER_ONLY))
    assert scale.weight is None

    await scale.on_bluetooth_data_received(None, bytearray(m.SPLIT_REMAINDER))
    assert scale.weight == pytest.approx(175.9)
