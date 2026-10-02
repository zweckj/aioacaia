# aioacaia

Async Python library for interacting with Acaia Bluetooth scales.

## Usage

```python
import asyncio

from aioacaia import AcaiaScale
from aioacaia.discovery import find_acaia_devices, is_new_scale


async def main() -> None:
    addresses = await find_acaia_devices()
    if not addresses:
        raise RuntimeError("No Acaia scale found")

    address = addresses[0]
    scale = AcaiaScale(
        address,
        is_new_style_scale=await is_new_scale(address),
    )
    await scale.connect()

    try:
        if not await scale.tare():
            raise RuntimeError("Scale did not confirm tare")
        if not await scale.start_stop_timer():
            raise RuntimeError("Scale did not confirm timer start")
        if not await scale.reset_timer():
            raise RuntimeError("Scale did not confirm timer reset")
    finally:
        await scale.disconnect()


asyncio.run(main())
```

`tare()`, `start_stop_timer()`, and `reset_timer()` return `True` once the scale
confirms the command, and `False` if no confirmation arrives within two seconds
or the connection drops. Resetting a running timer also waits for the restart to
be confirmed.

## State Updates

Pass a no-argument callback to receive notification when the scale state changes.
The latest values are available through `weight`, `timer`, `flow_rate`, and
`device_state`.

```python
import asyncio

from aioacaia import AcaiaScale


async def monitor(address: str) -> None:
    scale: AcaiaScale

    def state_changed() -> None:
        print(f"Weight: {scale.weight}")
        print(f"Timer: {scale.timer}")
        if scale.device_state is not None:
            print(f"Battery: {scale.device_state.battery_level}%")

    scale = AcaiaScale(address, notify_callback=state_changed)
    await scale.connect()
    try:
        await asyncio.Event().wait()
    finally:
        await scale.disconnect()


asyncio.run(monitor("AA:BB:CC:DD:EE:FF"))
```

`connect()` also accepts a raw Bleak notification callback. Supplying one bypasses
the built-in parsing and state updates.
