"""Tests for the main logic paths of aioacaia.scale."""

from unittest.mock import Mock

import pytest

from aioacaia.scale import AcaiaScale
from aioacaia.const import UnitMass
from tests.fixtures import messages as m

_ADDRESS = "aa:bb:cc:dd:ee:ff"


def _make_scale(**kwargs) -> AcaiaScale:
    """Create a scale bound to a dummy address."""
    return AcaiaScale(_ADDRESS, **kwargs)


async def test_receive_settings_updates_device_state():
    """A settings notification populates the device state."""
    scale = _make_scale()
    await scale.on_bluetooth_data_received(None, bytearray(m.SETTINGS_GRAMS))

    state = scale.device_state
    assert state is not None
    assert state.battery_level == 93
    assert state.units == UnitMass.GRAMS
    assert state.beeps is True
    assert state.auto_off_time == 5


async def test_receive_weight_updates_weight_and_history():
    """A weight notification updates the weight and history."""
    scale = _make_scale()
    await scale.on_bluetooth_data_received(None, bytearray(m.WEIGHT))

    assert scale.weight == pytest.approx(175.9)
    assert len(scale.weight_history) == 1


async def test_receive_start_button_starts_timer():
    """A start button notification starts the timer."""
    scale = _make_scale()
    await scale.on_bluetooth_data_received(None, bytearray(m.BUTTON_START))

    assert scale.timer_running is True
    assert scale._timer_start is not None


async def test_receive_stop_button_stops_timer():
    """A stop button notification stops the timer."""
    scale = _make_scale()
    scale.timer_running = True
    await scale.on_bluetooth_data_received(None, bytearray(m.BUTTON_STOP))

    assert scale.timer_running is False
    assert scale._timer_stop is not None


async def test_receive_reset_button_clears_timer():
    """A reset button notification clears the timer state."""
    scale = _make_scale()
    scale.timer_running = True
    scale._timer_start = 100.0
    scale._timer_stop = 200.0
    await scale.on_bluetooth_data_received(None, bytearray(m.BUTTON_RESET))

    assert scale.timer_running is False
    assert scale._timer_start is None
    assert scale._timer_stop is None


async def test_receive_invokes_notify_callback():
    """Receiving a valid message triggers the notify callback."""
    callback = Mock()
    scale = _make_scale(notify_callback=callback)
    await scale.on_bluetooth_data_received(None, bytearray(m.WEIGHT))

    callback.assert_called_once()


async def test_receive_ignores_non_header_short_message():
    """A short message without a header is ignored."""
    callback = Mock()
    scale = _make_scale(notify_callback=callback)
    await scale.on_bluetooth_data_received(None, bytearray(b"\x01\x02"))

    assert scale.weight is None
    assert scale._last_short_msg is None
    callback.assert_not_called()


def test_timer_while_running(monkeypatch):
    """While running, the timer reports elapsed time since start."""
    monkeypatch.setattr("aioacaia.scale.time.time", lambda: 1050.0)
    scale = _make_scale()
    scale._timer_start = 1000.0
    scale.timer_running = True

    assert scale.timer == 50


def test_timer_while_stopped():
    """While stopped, the timer reports the frozen elapsed time."""
    scale = _make_scale()
    scale._timer_start = 1000.0
    scale._timer_stop = 1042.0
    scale.timer_running = False

    assert scale.timer == 42


def test_flow_rate_none_without_enough_history():
    """Flow rate is None with fewer than four readings."""
    scale = _make_scale()
    scale.weight_history.extend([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])

    assert scale.flow_rate is None


def test_flow_rate_computes_rate():
    """Flow rate is computed from the weight history."""
    scale = _make_scale()
    scale.weight_history.extend([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)])

    assert scale.flow_rate == pytest.approx(1.0)


async def test_tare_enqueues_command():
    """Taring enqueues the tare command."""
    scale = _make_scale()
    scale.connected = True
    await scale.tare()

    char_id, payload = scale._queue.get_nowait()
    assert char_id == scale._default_char_id
    assert payload == scale._msg_types["tare"]


async def test_start_stop_timer_toggles_state():
    """start_stop_timer toggles the running state and enqueues commands."""
    scale = _make_scale()
    scale.connected = True

    await scale.start_stop_timer()
    assert scale.timer_running is True
    assert scale._timer_start is not None
    _, payload = scale._queue.get_nowait()
    assert payload == scale._msg_types["startTimer"]

    await scale.start_stop_timer()
    assert scale.timer_running is False
    assert scale._timer_stop is not None
    _, payload = scale._queue.get_nowait()
    assert payload == scale._msg_types["stopTimer"]


async def test_reset_timer_clears_state_and_enqueues():
    """Resetting the timer clears state and enqueues the reset command."""
    scale = _make_scale()
    scale.connected = True
    scale._timer_start = 100.0
    scale._timer_stop = 200.0

    await scale.reset_timer()

    assert scale._timer_start is None
    assert scale._timer_stop is None
    _, payload = scale._queue.get_nowait()
    assert payload == scale._msg_types["resetTimer"]


def test_device_disconnected_handler_resets_state():
    """Disconnecting resets connection state and notifies."""
    callback = Mock()
    scale = _make_scale(notify_callback=callback)
    scale.connected = True
    scale.timer_running = True

    scale.device_disconnected_handler()

    assert scale.connected is False
    assert scale.timer_running is False
    assert scale.last_disconnect_time is not None
    callback.assert_called_once()
