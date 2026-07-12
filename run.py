import asyncio

from aioacaia import AcaiaScale


async def main() -> None:
    with open("mac.txt", encoding="utf-8") as mac_file:
        mac = mac_file.read().strip()

    scale = AcaiaScale(address_or_ble_device=mac)
    await scale.connect()
    try:
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
    finally:
        await scale.disconnect()


asyncio.run(main())
