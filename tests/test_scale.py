"""Tests for the main logic paths of aioacaia.scale."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from bleak.exc import BleakDeviceNotFoundError, BleakError

from aioacaia import scale as scale_module
from aioacaia.const import UnitMass
from aioacaia.encoder import Command
from aioacaia.exceptions import AcaiaDeviceNotFound, AcaiaError
from aioacaia.scale import AcaiaScale
from tests.fixtures import messages as m

_ADDRESS = "aa:bb:cc:dd:ee:ff"


def _make_scale(**kwargs) -> AcaiaScale:
    """Create a scale bound to a dummy address."""
    return AcaiaScale(_ADDRESS, **kwargs)


def test_auth_command_is_instance_specific():
    """Each scale selects the auth command matching its generation."""
    new_style_scale = _make_scale(is_new_style_scale=True)
    old_style_scale = _make_scale(is_new_style_scale=False)

    assert new_style_scale._auth is Command.AUTH_PYXIS
    assert old_style_scale._auth is Command.AUTH_CLASSIC


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


async def test_timer_notification_preserves_weight_and_updates_timer(monkeypatch):
    """Timer-only notifications do not erase the latest weight."""
    monkeypatch.setattr("aioacaia.scale.time.monotonic", lambda: 1000.0)
    scale = _make_scale()
    await scale.on_bluetooth_data_received(None, bytearray(m.WEIGHT))

    await scale.on_bluetooth_data_received(None, bytearray(m.TIMER))

    assert scale.weight == pytest.approx(175.9)
    assert scale.timer == 90


async def test_button_without_weight_preserves_latest_weight():
    """Button notifications only replace weight when they include one."""
    scale = _make_scale()
    await scale.on_bluetooth_data_received(None, bytearray(m.WEIGHT))

    await scale.on_bluetooth_data_received(None, bytearray(m.BUTTON_START))

    assert scale.weight == pytest.approx(175.9)


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
    monkeypatch.setattr("aioacaia.scale.time.monotonic", lambda: 1050.0)
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


def test_flow_rate_skips_nonpositive_time_deltas():
    """Duplicate or backward timestamps do not cause invalid division."""
    scale = _make_scale()
    scale.weight_history.extend([(1.0, 1.0), (1.0, 2.0), (1.0, 3.0), (1.0, 4.0)])

    assert scale.flow_rate is None


async def test_tare_enqueues_command():
    """Taring enqueues the tare command."""
    scale = _make_scale()
    scale.connected = True
    await scale.tare()

    char_id, payload = scale._queue.get_nowait()
    assert char_id == scale._default_char_id
    assert payload == Command.TARE


async def test_process_queue_acknowledges_failed_write():
    """A failed write does not leave the queue join counter blocked."""
    scale = _make_scale()
    scale.connected = True
    client = AsyncMock()
    client.write_gatt_char.side_effect = BleakError("write failed")
    scale._client = client
    await scale._queue.put((scale._default_char_id, b"payload"))

    await scale.process_queue()
    await asyncio.wait_for(scale._queue.join(), timeout=0.1)

    assert scale.connected is False
    client.disconnect.assert_awaited_once()
    assert scale._client is None


async def test_connect_without_background_tasks_writes_setup_directly(monkeypatch):
    """A connection without workers still authenticates and disconnects cleanly."""
    client = AsyncMock()
    establish_connection = AsyncMock(return_value=client)
    monkeypatch.setattr(scale_module, "establish_connection", establish_connection)
    device = SimpleNamespace(name="Test Scale", address=_ADDRESS)
    scale = AcaiaScale(device)

    await scale.connect(setup_tasks=False)

    assert scale.connected is True
    assert scale._queue.empty()
    assert client.write_gatt_char.await_count == 2

    await asyncio.wait_for(scale.disconnect(), timeout=0.1)
    client.disconnect.assert_awaited_once()


async def test_connect_subscription_failure_rolls_back(monkeypatch):
    """A notification subscription failure closes the client and resets state."""
    client = AsyncMock()
    client.start_notify.side_effect = BleakError("subscription failed")
    monkeypatch.setattr(
        scale_module, "establish_connection", AsyncMock(return_value=client)
    )
    device = SimpleNamespace(name="Test Scale", address=_ADDRESS)
    scale = AcaiaScale(device)

    with pytest.raises(AcaiaError, match="Error setting up connection"):
        await scale.connect(setup_tasks=False)

    assert scale.connected is False
    assert scale._client is None
    client.disconnect.assert_awaited_once()


async def test_connect_waits_out_reconnect_cooldown(monkeypatch):
    """A recent disconnect delays setup instead of returning disconnected."""
    client = AsyncMock()
    monkeypatch.setattr(
        scale_module, "establish_connection", AsyncMock(return_value=client)
    )
    sleep = AsyncMock()
    monkeypatch.setattr(scale_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(scale_module.time, "time", lambda: 110.0)
    device = SimpleNamespace(name="Test Scale", address=_ADDRESS)
    scale = AcaiaScale(device)
    scale.last_disconnect_time = 100.0

    await scale.connect(setup_tasks=False)

    assert sleep.await_args_list[0].args == (5.0,)
    assert scale.connected is True


async def test_connect_preserves_device_not_found(monkeypatch):
    """A missing device is reported with the library's specific exception."""
    monkeypatch.setattr(
        scale_module,
        "establish_connection",
        AsyncMock(side_effect=BleakDeviceNotFoundError(_ADDRESS)),
    )
    device = SimpleNamespace(name="Test Scale", address=_ADDRESS)
    scale = AcaiaScale(device)

    with pytest.raises(AcaiaDeviceNotFound):
        await scale.connect(setup_tasks=False)


async def test_start_stop_timer_toggles_state():
    """start_stop_timer toggles the running state and enqueues commands."""
    scale = _make_scale()
    scale.connected = True

    await scale.start_stop_timer()
    assert scale.timer_running is True
    assert scale._timer_start is not None
    _, payload = scale._queue.get_nowait()
    assert payload == Command.START_TIMER

    await scale.start_stop_timer()
    assert scale.timer_running is False
    assert scale._timer_stop is not None
    _, payload = scale._queue.get_nowait()
    assert payload == Command.STOP_TIMER


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
    assert payload == Command.RESET_TIMER


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
