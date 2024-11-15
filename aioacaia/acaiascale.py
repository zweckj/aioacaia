"""Client to interact with Acaia scales."""

from __future__ import annotations

import asyncio
import logging
import time

from collections import deque
from collections.abc import Awaitable, Callable

from dataclasses import dataclass

from bleak import BleakClient, BleakGATTCharacteristic, BLEDevice
from bleak.exc import BleakDeviceNotFoundError, BleakError

from .const import (
    DEFAULT_CHAR_ID,
    HEADER1,
    HEADER2,
    HEARTBEAT_INTERVAL,
    NOTIFY_CHAR_ID,
    OLD_STYLE_CHAR_ID,
)
from .exceptions import (
    AcaiaDeviceNotFound,
    AcaiaError,
    AcaiaMessageError,
    AcaiaMessageTooLong,
    AcaiaMessageTooShort,
)
from .const import UnitMass
from .decode import Message, Settings, decode
from .helpers import encode, encode_id, encode_notification_request, derive_model_name

_LOGGER = logging.getLogger(__name__)


@dataclass(kw_only=True)
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

    _msg_types = {
        "tare": encode(4, [0]),
        "startTimer": encode(13, [0, 0]),
        "stopTimer": encode(13, [0, 2]),
        "resetTimer": encode(13, [0, 1]),
        "heartbeat": encode(0, [2, 0]),
        "getSettings": encode(6, [0] * 16),
        "notificationRequest": encode_notification_request(),
    }

    def __init__(
        self,
        address_or_ble_device: str | BLEDevice,
        name: str | None = None,
        is_new_style_scale: bool = True,
        notify_callback: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the scale."""

        self._is_new_style_scale = is_new_style_scale
        self._client: BleakClient | None = None

        self.address_or_ble_device = address_or_ble_device
        self.model = derive_model_name(name)
        self.name = name

        # tasks
        self.heartbeat_task: asyncio.Task | None = None
        self.process_queue_task: asyncio.Task | None = None

        # timer related
        self.timer_running = False
        self._timer_start: float | None = None
        self._timer_stop: float | None = None

        # connection diagnostics
        self.connected = False
        self._timestamp_last_command: float | None = None
        self.last_disconnect_time: float | None = None

        self._device_state: AcaiaDeviceState | None = None
        self._weight: float | None = None

        # flow rate
        self.weight_history = deque(maxlen=20)  # Limit to 20 entries

        # queue
        self._queue: asyncio.Queue = asyncio.Queue()
        self._add_to_queue_lock = asyncio.Lock()

        self._last_short_msg: bytearray | None = None

        self._msg_types["auth"] = encode_id(is_pyxis_style=is_new_style_scale)

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
            return int(time.time() - self._timer_start)
        if self._timer_stop is None:
            return 0

        return int(self._timer_stop - self._timer_start)
    
    @property
    def flow_rate(self) -> float:

        if len(self.weight_history) < 4:
            return None

        now = time.time()
        flows = []

        # Calculate flow rates using 3 readings ago
        for i in range(3, len(self.weight_history)):
            prev_time, prev_weight = self.weight_history[i - 3]
            curr_time, curr_weight = self.weight_history[i]

            time_diff = curr_time - prev_time
            weight_diff = curr_weight - prev_weight

            # Ignore weights older than 5 seconds
            if now - prev_time > 5:
                continue

            # Validate weight difference and flow rate limits
            flow = weight_diff / time_diff
            if flow <= 20.0:  # Flow rate limit
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
        self.last_disconnect_time = time.time()
        self.async_empty_queue_and_cancel_tasks()
        if notify and self._notify_callback:
            self._notify_callback()

    async def _write_msg(self, char_id: str, payload: bytearray) -> None:
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

    def async_empty_queue_and_cancel_tasks(self) -> None:
        """Empty the queue."""

        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()

        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()

        if self.process_queue_task and not self.process_queue_task.done():
            self.process_queue_task.cancel()

    async def process_queue(self) -> None:
        """Task to process the queue in the background."""
        while True:
            try:
                if not self.connected:
                    self.async_empty_queue_and_cancel_tasks()
                    return

                char_id, payload = await self._queue.get()
                await self._write_msg(char_id, payload)
                self._queue.task_done()
                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                self.connected = False
                return
            except (AcaiaDeviceNotFound, AcaiaError) as ex:
                self.connected = False
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

        if self.last_disconnect_time and self.last_disconnect_time > (time.time() - 15):
            _LOGGER.debug(
                "Scale has recently been disconnected, waiting 15 seconds before reconnecting"
            )
            return

        self._client = BleakClient(
            address_or_ble_device=self.address_or_ble_device,
            disconnected_callback=self.device_disconnected_handler,
        )

        try:
            await self._client.connect()
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

        self.connected = True
        _LOGGER.debug("Connected to Acaia scale")

        if callback is None:
            callback = self.on_bluetooth_data_received
        try:
            await self._client.start_notify(
                char_specifier=self._notify_char_id,
                callback=(
                    self.on_bluetooth_data_received if callback is None else callback
                ),
            )
            await asyncio.sleep(0.1)
        except BleakError as ex:
            msg = "Error subscribing to notifications"
            _LOGGER.debug("%s: %s", msg, ex)
            raise AcaiaError(msg) from ex

        try:
            await self.auth()
            if callback is not None:
                await self.send_weight_notification_request()
        except BleakDeviceNotFoundError as ex:
            raise AcaiaDeviceNotFound("Device not found") from ex
        except BleakError as ex:
            raise AcaiaError("Error during authentication") from ex

        if setup_tasks:
            self._setup_tasks()

    def _setup_tasks(self) -> None:
        """Setup background tasks"""
        if not self.heartbeat_task or self.heartbeat_task.done():
            self.heartbeat_task = asyncio.create_task(self.send_heartbeats())
        if not self.process_queue_task or self.process_queue_task.done():
            self.process_queue_task = asyncio.create_task(self.process_queue())

    async def auth(self) -> None:
        """Send auth message to scale, if subscribed to notifications returns Settings object"""
        await self._queue.put((self._default_char_id, self._msg_types["auth"]))

    async def send_weight_notification_request(self) -> None:
        """Tell the scale to send weight notifications"""

        await self._queue.put(
            (self._default_char_id, self._msg_types["notificationRequest"])
        )

    async def send_heartbeats(self) -> None:
        """Task to send heartbeats in the background."""
        while True:
            try:
                if not self.connected:
                    return

                async with self._add_to_queue_lock:
                    _LOGGER.debug("Sending heartbeat")
                    if self._is_new_style_scale:
                        await self._queue.put(
                            (self._default_char_id, self._msg_types["auth"])
                        )

                    await self._queue.put(
                        (self._default_char_id, self._msg_types["heartbeat"])
                    )

                    if self._is_new_style_scale:
                        await self._queue.put(
                            (self._default_char_id, self._msg_types["getSettings"])
                        )
                await asyncio.sleep(
                    HEARTBEAT_INTERVAL if not self._is_new_style_scale else 1,
                )
            except asyncio.CancelledError:
                self.connected = False
                return
            except asyncio.QueueFull as ex:
                self.connected = False
                _LOGGER.debug("Error sending heartbeat: %s", ex)
                return

    async def disconnect(self) -> None:
        """Clean disconnect from the scale"""

        _LOGGER.debug("Disconnecting from scale")
        self.connected = False
        await self._queue.join()
        if not self._client:
            return
        try:
            await self._client.disconnect()
        except BleakError as ex:
            _LOGGER.debug("Error disconnecting from device: %s", ex)
        else:
            _LOGGER.debug("Disconnected from scale")

    async def tare(self) -> None:
        """Tare the scale."""
        if not self.connected:
            await self.connect()
        async with self._add_to_queue_lock:
            await self._queue.put((self._default_char_id, self._msg_types["tare"]))

    async def start_stop_timer(self) -> None:
        """Start/Stop the timer."""
        if not self.connected:
            await self.connect()

        if not self.timer_running:
            _LOGGER.debug('Sending "start" message.')

            async with self._add_to_queue_lock:
                await self._queue.put(
                    (self._default_char_id, self._msg_types["startTimer"])
                )
            self.timer_running = True
            if not self._timer_start:
                self._timer_start = time.time()
        else:
            _LOGGER.debug('Sending "stop" message.')
            async with self._add_to_queue_lock:
                await self._queue.put(
                    (self._default_char_id, self._msg_types["stopTimer"])
                )
            self.timer_running = False
            self._timer_stop = time.time()

    async def reset_timer(self) -> None:
        """Reset the timer."""
        if not self.connected:
            await self.connect()
        async with self._add_to_queue_lock:
            await self._queue.put(
                (self._default_char_id, self._msg_types["resetTimer"])
            )
        self._timer_start = None
        self._timer_stop = None

        if self.timer_running:
            async with self._add_to_queue_lock:
                await self._queue.put(
                    (self._default_char_id, self._msg_types["startTimer"])
                )
            self._timer_start = time.time()

    async def on_bluetooth_data_received(
        self,
        characteristic: BleakGATTCharacteristic,  # pylint: disable=unused-argument
        data: bytearray,
    ) -> None:
        """Receive data from scale."""

        # For some scales the header is sent and then in next message the content
        if (
            self._last_short_msg is not None
            and self._last_short_msg[0] == HEADER1
            and self._last_short_msg[1] == HEADER2
        ):
            data = self._last_short_msg + data
            self._last_short_msg = None
            _LOGGER.debug("Restored message from previous data: %s", data)

        try:
            msg, _ = decode(data)
        except AcaiaMessageTooShort as ex:
            if ex.bytes_recvd[0] != HEADER1 or ex.bytes_recvd[1] != HEADER2:
                _LOGGER.debug("Non-header message too short: %s", ex.bytes_recvd)
            else:
                self._last_short_msg = ex.bytes_recvd
            return
        except AcaiaMessageTooLong as ex:
            _LOGGER.debug("%s: %s", ex.message, ex.bytes_recvd)
        except AcaiaMessageError as ex:
            _LOGGER.warning("%s: %s", ex.message, ex.bytes_recvd)

        if isinstance(msg, Settings):
            self._device_state = AcaiaDeviceState(
                battery_level=msg.battery,
                units=UnitMass(msg.units),
                beeps=msg.beep_on,
                auto_off_time=msg.auto_off,
            )
            _LOGGER.debug(
                "Got battery level %s, units %s", str(msg.battery), str(msg.units)
            )

        elif isinstance(msg, Message):
            self._weight = msg.value
            timestamp = time.time()

            # Check if weight is increasing before appending
            if not self.weight_history or msg.value > self.weight_history[-1][1]:
                self.weight_history.append((timestamp, msg.value)) 
            elif msg.value < self.weight_history[-1][1] - 1: 
                # Clear history if weight decreases (1gr margin error)
                self.weight_history.clear()
                self.weight_history.append((timestamp, msg.value))

            if msg.timer_running is not None:
                self.timer_running = msg.timer_running
            _LOGGER.debug("Got weight %s", str(msg.value))

        if self._notify_callback is not None:
            self._notify_callback()
