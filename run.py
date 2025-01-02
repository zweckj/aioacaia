import asyncio

from aioacaia import AcaiaScale
from aioacaia.decode import notification_handler
from aioacaia.decode import decode, Message
from aioacaia.helpers import is_new_scale


async def main():
    # print(await is_new_scale("60:8A:10:4E:24:50"))
    # exit(0)

    # # settings, arr = decode(bytearray(b"\xef\xdd\x08\t]\x02\x02\x01\x00\x01\x01\x00\r`"))
    # scale = AcaiaScale("aa:bb:cc:dd:ee:ff")
    # await scale.on_bluetooth_data_received(None, bytearray(b"\xef\xdd\x0c"))
    # res = await scale.on_bluetooth_data_received(
    #     None, bytearray(b"\x0c\x05\xdf\x06\x00\x00\x01\x00\x07\x00\x00\x02\xf3\r")
    # )
    # decode(
    #     bytearray(b"\xef\xdd\x0c\x0c\x05\xdf\x06\x00\x00\x01\x00\x07\x00\x00\x02\xf3\r")
    # )
    # # Message(5, bytearray(b"\xdf\x06\x00\x00\x01\x00\x07\x00\x00\x02\xf3\r"))
    # # Message(5, b"\xa1\x10\x00\x00\x01\x01\x07\x00\x00\x02\xb5\x18")
    # # Message(5, bytearray(b"]\x07\x00\x00\x01\x00\x07\x00\x00\x02q\x0e"))
    # # 0 = 12
    # # 1 = 5
    # Message(5, b"\x00\x00\x00\x00\x01\x00\x07\x00\x00\x02\x14\x07")
    # decode(bytearray(b"\x0c\x05\x00\x00\x00\x00\x01\x00\x07\x00\x00\x02\x14\x07"))
    # exit(0)

    with open("mac.txt", "r") as f:
        mac = f.read().strip()

    scale = AcaiaScale(address_or_ble_device=mac)
    await scale.connect()

    # await asyncio.sleep(1)
    # await scale.tare()
    await asyncio.sleep(300)

    await asyncio.sleep(1)
    print("starting Timer...")
    await scale.start_stop_timer()

    await asyncio.sleep(21)

    print("stopping Timer...")
    await scale.start_stop_timer()

    await asyncio.sleep(5)
    print("resetting Timer...")
    await scale.reset_timer()
    await asyncio.sleep(5)
    print("starting Timer...")
    await scale.start_stop_timer()
    await asyncio.sleep(30)
    print("stopping Timer...")
    await scale.start_stop_timer()

    # print(f"Timer running: {scale._timer_running}")
    # print(scale.timer)
    # print("resetting Timer...")
    # await scale.resetTimer()

    # await asyncio.sleep(2)
    # print("starting Timer...")
    # await scale.auth()
    # await scale.startStopTimer()
    # print(scale.timer)

    # await asyncio.sleep(5)
    # print("stopping Timer...")
    # await scale.startStopTimer()
    # # await asyncio.sleep(5)
    # # await scale.startStopTimer()
    # print(scale.timer)

    await scale.disconnect()


asyncio.run(main())
