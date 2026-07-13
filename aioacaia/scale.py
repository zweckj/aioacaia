"""Client to interact with Acaia scales."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakDeviceNotFoundError, BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import (
    DEFAULT_CHAR_ID,
    HEADER1,
    HEADER2,
    HEARTBEAT_INTERVAL,
    NOTIFY_CHAR_ID,
    OLD_STYLE_CHAR_ID,
    UnitMass,
)
from .discovery import derive_model_name
from .encoder import Command
from .exceptions import (
    AcaiaDeviceNotFound,
    AcaiaError,
    AcaiaMessageError,
    AcaiaMessageTooLong,
    AcaiaMessageTooShort,
)
from .messages import (
    ButtonMessage,
    ButtonType,
    ScaleMessage,
    Settings,
    TimerMessage,
    WeightMessage,
)
from .parser import decode

_LOGGER = logging.getLogger(__name__)

_HEADER = bytes((HEADER1, HEADER2))


@dataclass(kw_only=True, slots=True)
class AcaiaDeviceState:
    """Data class for acaia scale info data."""

    battery_level: int
    units: UnitMass
    beeps: bool = True
    auto_off_time: int = 0


class AcaiaScale:
    """Representation of an acaia scale."""

    _default_char_id = DEFAULT_CHAR_ID
    _notify_char_id = NOTIFY_CHAR_ID

    def __init__(
        self,
        address_or_ble_device: str | BLEDevice,
        name: str | None = None,
        is_new_style_scale: bool = True,
        notify_callback: Callable[[], None] | None = None,
        scanner: BleakScanner | None = None,
    ) -> None:
        """Initialize the scale."""

        self._is_new_style_scale = is_new_style_scale
        self._client: BleakClient | None = None
        self._scanner = scanner

        self.address_or_ble_device = address_or_ble_device
        self.model = derive_model_name(name)
        self.name = name

        # tasks
        self.heartbeat_task: asyncio.Task[None] | None = None
        self.process_queue_task: asyncio.Task[None] | None = None

        # timer related
        self.timer_running = False
        self._timer_start: float | None = None
        self._timer_stop: float | None = None
        self._button_pressed = False

        # connection diagnostics
        self.connected = False
        self._timestamp_last_command: float | None = None
        self.last_disconnect_time: float | None = None

        self._device_state: AcaiaDeviceState | None = None
        self._weight: float | None = None

        # flow rate
        self.weight_history: deque[tuple[float, float]] = deque(
            maxlen=20
        )  # Limit to 20 entries

        # queue
        self._queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue(maxsize=100)
        self._add_to_queue_lock = asyncio.Lock()

        self._last_short_msg: bytearray | None = None

        self._auth = Command.AUTH_PYXIS if is_new_style_scale else Command.AUTH_CLASSIC

        if not is_new_style_scale:
            # for old style scales, the default char id is the same as the notify char id
            self._default_char_id = self._notify_char_id = OLD_STYLE_CHAR_ID

        self._notify_callback: Callable[[], None] | None = notify_callback

    @property
    def mac(self) -> str:
        """Return the mac address of the scale in upper case."""
        return (
            self.address_or_ble_device.upper()
            if isinstance(self.address_or_ble_device, str)
            else self.address_or_ble_device.address.upper()
        )

    @property
    def device_state(self) -> AcaiaDeviceState | None:
        """Return the device info of the scale."""
        return self._device_state

    @property
    def weight(self) -> float | None:
        """Return the weight of the scale."""
        return self._weight

    @property
    def timer(self) -> int:
        """Return the current timer value in seconds."""
        if self._timer_start is None:
            return 0
        if self.timer_running:
            return int(time.monotonic() - self._timer_start)
        if self._timer_stop is None:
            return 0

        return int(self._timer_stop - self._timer_start)

    @property
    def flow_rate(self) -> float | None:
        """Calculate the current flow rate."""
        flows = []

        if len(self.weight_history) < 4:
            return None

        # Calculate flow rates using 3 readings ago
        for i in range(3, len(self.weight_history)):
            prev_time, prev_weight = self.weight_history[i - 3]
            curr_time, curr_weight = self.weight_history[i]

            time_diff = curr_time - prev_time
            weight_diff = curr_weight - prev_weight

            # Validate weight difference and flow rate limits
            if time_diff <= 0:
                continue
            flow = weight_diff / time_diff
            if 0 <= flow <= 20.0:  # Flow rate limit
                flows.append(flow)

        if not flows:
            return None

        # Compute the Exponential Moving Average (EMA)
        alpha = 2 / (len(flows) + 1)  # EMA weighting factor
        ema = flows[0]  # Initialize EMA with the first flow rate

        for flow in flows[1:]:
            ema = alpha * flow + (1 - alpha) * ema

        _LOGGER.debug("Flow rate: %.2f g/s", ema)
        return ema

    def device_disconnected_handler(
        self,
        client: BleakClient | None = None,  # pylint: disable=unused-argument
        notify: bool = True,
    ) -> None:
        """Callback for device disconnected."""

        _LOGGER.debug(
            "Scale with address %s disconnected through disconnect handler",
            self.mac,
        )
        self.timer_running = False
        self.connected = False
        self._client = None
        self.last_disconnect_time = time.time()
        self._cancel_background_tasks()
        self._drain_queue()
        if notify and self._notify_callback:
            self._notify_callback()

    async def _write_msg(self, char_id: str, payload: bytes) -> None:
        """wrapper for writing to the device."""
        if self._client is None:
            raise AcaiaError("Client not initialized")
        try:
            await self._client.write_gatt_char(char_id, payload)
            self._timestamp_last_command = time.time()
        except BleakDeviceNotFoundError as ex:
            self.connected = False
            raise AcaiaDeviceNotFound("Device not found") from ex
        except BleakError as ex:
            self.connected = False
            raise AcaiaError("Error writing to device") from ex
        except TimeoutError as ex:
            self.connected = False
            raise AcaiaError("Timeout writing to device") from ex
        except Exception as ex:
            self.connected = False
            raise AcaiaError("Unknown error writing to device") from ex

    def _drain_queue(self) -> None:
        """Discard and acknowledge all queued commands."""
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._queue.task_done()

    def _cancel_background_tasks(self) -> list[asyncio.Task[None]]:
        """Cancel background tasks other than the calling task."""
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None

        tasks = []
        for task in (self.heartbeat_task, self.process_queue_task):
            if task and task is not current_task and not task.done():
                task.cancel()
                tasks.append(task)
        return tasks

    async def _disconnect_client(self) -> None:
        """Disconnect and clear the current BLE client."""
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            await client.disconnect()
        except BleakError as ex:
            _LOGGER.debug("Error disconnecting from device: %s", ex)

    async def process_queue(self) -> None:
        """Task to process the queue in the background."""
        while self.connected:
            try:
                char_id, payload = await self._queue.get()
                try:
                    await self._write_msg(char_id, payload)
                finally:
                    self._queue.task_done()
                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                self.connected = False
                return
            except (AcaiaDeviceNotFound, AcaiaError) as ex:
                self.connected = False
                self._cancel_background_tasks()
                self._drain_queue()
                await self._disconnect_client()
                _LOGGER.debug("Error writing to device: %s", ex)
                return

    async def connect(
        self,
        callback: (
            Callable[[BleakGATTCharacteristic, bytearray], Awaitable[None] | None]
            | None
        ) = None,
        setup_tasks: bool = True,
    ) -> None:
        """Connect the bluetooth client."""

        if self.connected:
            return

        if self.last_disconnect_time:
            reconnect_delay = 15 - (time.time() - self.last_disconnect_time)
        else:
            reconnect_delay = 0
        if reconnect_delay > 0:
            _LOGGER.debug(
                "Scale has recently been disconnected, waiting %.1f seconds before reconnecting",
                reconnect_delay,
            )
            await asyncio.sleep(reconnect_delay)

        if isinstance(self.address_or_ble_device, str):
            if not self._scanner:
                self._scanner = BleakScanner()
            device = await self._scanner.find_device_by_address(
                self.address_or_ble_device
            )
            if not device:
                raise AcaiaDeviceNotFound(
                    f"Device with address {self.address_or_ble_device} not found"
                )
            self.address_or_ble_device = device

        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                self.address_or_ble_device,
                self.address_or_ble_device.name or "Unknown",
                max_attempts=3,
                disconnected_callback=self.device_disconnected_handler,
            )
        except BleakDeviceNotFoundError as ex:
            raise AcaiaDeviceNotFound("Device not found") from ex
        except BleakError as ex:
            msg = "Error during connecting to device"
            _LOGGER.debug("%s: %s", msg, ex)
            raise AcaiaError(msg) from ex
        except TimeoutError as ex:
            msg = "Timeout during connecting to device"
            _LOGGER.debug("%s: %s", msg, ex)
            raise AcaiaError(msg) from ex
        except Exception as ex:
            msg = "Unknown error during connecting to device"
            _LOGGER.debug("%s: %s", msg, ex)
            raise AcaiaError(msg) from ex

        self._client = client
        if callback is None:
            callback = self.on_bluetooth_data_received
        try:
            await client.start_notify(
                char_specifier=self._notify_char_id,
                callback=callback,
            )
            await asyncio.sleep(0.1)
            await self._write_msg(self._default_char_id, self._auth)
            await asyncio.sleep(0.1)
            await self._write_msg(self._default_char_id, Command.NOTIFICATION_REQUEST)
        except asyncio.CancelledError:
            await self._disconnect_client()
            raise
        except BleakDeviceNotFoundError as ex:
            await self._disconnect_client()
            raise AcaiaDeviceNotFound("Device not found") from ex
        except AcaiaError:
            await self._disconnect_client()
            raise
        except BleakError as ex:
            await self._disconnect_client()
            raise AcaiaError("Error setting up connection") from ex
        except TimeoutError as ex:
            await self._disconnect_client()
            raise AcaiaError("Timeout setting up connection") from ex

        self.connected = True
        _LOGGER.debug("Connected to Acaia scale")

        if setup_tasks:
            self._setup_tasks()

    def _setup_tasks(self) -> None:
        """Setup background tasks"""
        if not self.heartbeat_task or self.heartbeat_task.done():
            self.heartbeat_task = asyncio.create_task(self.send_heartbeats())
        if not self.process_queue_task or self.process_queue_task.done():
            self.process_queue_task = asyncio.create_task(self.process_queue())

    def _command(self, command: Command) -> tuple[str, bytes]:
        """Build a (characteristic, payload) command tuple."""
        return (self._default_char_id, command)

    async def _enqueue_command(self, command: Command) -> None:
        """Queue a single command, serialising access with the queue lock."""
        async with self._add_to_queue_lock:
            await self._queue.put(self._command(command))

    async def _ensure_connected(self) -> None:
        """Connect on demand before sending a command."""
        if not self.connected:
            await self.connect()

    async def auth(self) -> None:
        """Send auth message to scale, if subscribed to notifications returns Settings object"""
        await self._enqueue_command(self._auth)

    async def send_weight_notification_request(self) -> None:
        """Tell the scale to send weight notifications"""
        await self._enqueue_command(Command.NOTIFICATION_REQUEST)

    async def send_heartbeats(self) -> None:
        """Task to send heartbeats in the background."""
        while True:
            try:
                if not self.connected:
                    return

                async with self._add_to_queue_lock:
                    _LOGGER.debug("Sending heartbeat")
                    if self._is_new_style_scale:
                        await self._queue.put(self._command(self._auth))
                    await self._queue.put(self._command(Command.HEARTBEAT))
                    if self._is_new_style_scale:
                        await self._queue.put(self._command(Command.GET_SETTINGS))
                await asyncio.sleep(
                    HEARTBEAT_INTERVAL if not self._is_new_style_scale else 1,
                )
            except asyncio.CancelledError:
                self.connected = False
                return

    async def disconnect(self) -> None:
        """Clean disconnect from the scale"""

        _LOGGER.debug("Disconnecting from scale")
        self.connected = False
        tasks = self._cancel_background_tasks()
        self._drain_queue()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._drain_queue()
        await self._queue.join()
        await self._disconnect_client()
        _LOGGER.debug("Disconnected from scale")

    def _start_timer(self) -> None:
        """Start or resume the local timer from any stored elapsed time."""
        self.timer_running = True
        if self._timer_start is not None and self._timer_stop is not None:
            self._timer_start = time.monotonic() - (
                self._timer_stop - self._timer_start
            )
        else:
            self._timer_start = time.monotonic()

    async def tare(self) -> None:
        """Tare the scale."""
        await self._ensure_connected()
        await self._enqueue_command(Command.TARE)

    async def start_stop_timer(self) -> None:
        """Start/Stop the timer."""
        await self._ensure_connected()

        if not self.timer_running:
            _LOGGER.debug('Sending "start" message.')
            await self._enqueue_command(Command.START_TIMER)
            self._start_timer()
        else:
            _LOGGER.debug('Sending "stop" message.')
            await self._enqueue_command(Command.STOP_TIMER)
            self.timer_running = False
            self._timer_stop = time.monotonic()

    async def reset_timer(self) -> None:
        """Reset the timer."""
        await self._ensure_connected()
        await self._enqueue_command(Command.RESET_TIMER)
        self._timer_start = None
        self._timer_stop = None

        if self.timer_running:
            await self._enqueue_command(Command.START_TIMER)
            self._timer_start = time.monotonic()

    async def on_bluetooth_data_received(
        self,
        _: BleakGATTCharacteristic,
        data: bytearray,
    ) -> None:
        """Receive data from the scale and update state for each message."""
        for msg in self._extract_messages(data):
            self._apply_message(msg)
            if self._notify_callback is not None:
                self._notify_callback()

    def _extract_messages(
        self, data: bytearray
    ) -> Iterator[ScaleMessage | Settings | None]:
        """Yield decoded frames from a notification, buffering partial ones."""
        pending = (self._last_short_msg or bytearray()) + data
        self._last_short_msg = None

        while pending:
            start = pending.find(_HEADER)
            if start < 0:
                if pending[-1] == HEADER1:
                    self._last_short_msg = bytearray((HEADER1,))
                else:
                    _LOGGER.debug("Ignoring non-header notification: %s", pending)
                return
            if start > 0:
                _LOGGER.debug("Ignoring %s bytes before header", start)
                pending = pending[start:]

            try:
                msg, pending = decode(pending)
            except AcaiaMessageTooShort:
                self._last_short_msg = pending
                return
            except AcaiaMessageTooLong:
                next_pending = self._skip_to_next_frame(pending)
                if next_pending is None:
                    return
                pending = next_pending
                continue
            except AcaiaMessageError as ex:
                _LOGGER.warning("%s: %s", ex.message, ex.bytes_recvd)
                pending = pending[2:]
                continue

            yield msg

    def _skip_to_next_frame(self, pending: bytearray) -> bytearray | None:
        """Advance past an over-long frame to the next decodable header.

        Returns the remaining bytes, or None when no further frame is found and
        the buffer has been stored for the next notification.
        """
        next_start = pending.find(_HEADER, 2)
        while next_start >= 0:
            try:
                decode(pending[next_start:])
            except AcaiaMessageError:
                next_start = pending.find(_HEADER, next_start + 2)
            else:
                break
        if next_start < 0:
            self._last_short_msg = pending
            return None
        _LOGGER.debug("Discarding incomplete frame before next header")
        return pending[next_start:]

    def _apply_message(self, msg: ScaleMessage | Settings | None) -> None:
        """Update scale state from a single decoded message."""
        match msg:
            case Settings():
                self._device_state = AcaiaDeviceState(
                    battery_level=msg.battery,
                    units=UnitMass(msg.units),
                    beeps=msg.beep_on,
                    auto_off_time=msg.auto_off,
                )
                _LOGGER.debug("Got battery level %s, units %s", msg.battery, msg.units)
            case WeightMessage():
                self._update_weight(msg.weight)
            case TimerMessage():
                self._update_timer(msg.time)
            case ButtonMessage():
                if msg.weight is not None:
                    self._update_weight(msg.weight)
                self._handle_button(msg.button, msg.timer_running, msg.time)

    def _update_weight(self, weight: float | None) -> None:
        """Store the latest weight and update the flow-rate history."""
        self._weight = weight
        timestamp = time.monotonic()

        # add to weight history for flow rate calculation
        if weight is not None:
            if self.weight_history:
                # Check if weight is increasing before appending
                if weight > self.weight_history[-1][1]:
                    self.weight_history.append((timestamp, weight))
                elif weight < self.weight_history[-1][1] - 1:
                    # Clear history if weight decreases (1gr margin error)
                    self.weight_history.clear()
                    self.weight_history.append((timestamp, weight))
            else:
                self.weight_history.append((timestamp, weight))
        # Remove old readings (more than 5 seconds)
        while self.weight_history and (timestamp - self.weight_history[0][0] > 5):
            self.weight_history.popleft()
        _LOGGER.debug("Got weight %s", str(weight))

    def _update_timer(self, elapsed_time: float) -> None:
        """Anchor the local timer to a value reported by the scale."""
        now = time.monotonic()
        self._timer_start = now - elapsed_time
        self._timer_stop = None if self.timer_running else now

    def _handle_button(
        self,
        button: ButtonType,
        timer_running: bool | None,
        elapsed_time: float | None,
    ) -> None:
        """Update the timer state from a physical button press."""

        def reset() -> None:
            """Physically reset the timer."""
            self._timer_start = None
            self._timer_stop = None
            self.timer_running = False
            self._button_pressed = False

        def reset_on_power_button() -> None:
            """Pressing the power button two consecutive times resets the timer."""
            if self._button_pressed:
                reset()
            else:
                self._button_pressed = True

        if button == ButtonType.START:
            self._start_timer()
            reset_on_power_button()
        elif button == ButtonType.STOP:
            self.timer_running = False
            if elapsed_time is None:
                self._timer_stop = time.monotonic()
            else:
                self._update_timer(elapsed_time)
            reset_on_power_button()
        elif button == ButtonType.RESET:
            reset()

        if timer_running is not None:
            self.timer_running = timer_running
