# aioacaia

Async implementation of [pyacaia](https://github.com/lucapinello/pyacaia), based on `asyncio` and `bleak`

# Usage

```python
import asyncio
from aioacaia import AcaiaScale
from aioacaia.helpers import find_acaia_devices

async def main()
  addresses = await find_acaia_devices()
  address = addresses[0]
  scale = await AcaiaScale.create(mac=address, is_new_style_scale=False)
  await scale.tare()
  await scale.startStopTimer()
  await scale.resetTimer()

asyncio.run(main())
```

# Callback

Weight and settings are available, if you pass a callback function to the constructor.
In that callback you will either receive objects of type `Message` or `Settings`. A sample notification handler can look like this and can also be found in `decode.py`

```python
def notification_handler(sender, data) -> None:
    msg = decode(data)[0]
    if isinstance(msg, Settings):
        print(f"Battery: {msg.battery}")
        print(f"Unit: {msg.units}")
    elif isinstance(msg, Message):
        print(f"Weight: {msg.value}")

scale = await AcaiaScale.create(mac=address, callback=notification_handler)
```
