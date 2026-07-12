import asyncio

from aioacaia import AcaiaScale


async def main():
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
