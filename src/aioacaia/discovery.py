"""Discover and identify Acaia scales over BLE."""

import logging

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakDeviceNotFoundError, BleakError

from .const import DEFAULT_CHAR_ID, OLD_STYLE_CHAR_ID, SCALE_START_NAMES
from .exceptions import AcaiaDeviceNotFound, AcaiaError, AcaiaUnknownDevice

_LOGGER = logging.getLogger(__name__)


async def find_acaia_devices(
    timeout: float = 10, scanner: BleakScanner | None = None
) -> list[str]:
    """Find Acaia devices and return their addresses."""
    _LOGGER.debug("Looking for ACAIA devices")
    if scanner is None:
        async with BleakScanner() as scanner:
            return await scan(scanner, timeout)
    return await scan(scanner, timeout)


async def scan(scanner: BleakScanner, timeout: float) -> list[str]:
    """Scan for Acaia devices with the given scanner and return their addresses."""
    addresses = []
    devices = await scanner.discover(timeout=timeout)
    for device in devices:
        if device.name and any(
            device.name.startswith(name) for name in SCALE_START_NAMES
        ):
            _LOGGER.debug("Found Acaia device %s (%s)", device.name, device.address)
            addresses.append(device.address)
    return addresses


async def is_new_scale(address_or_ble_device: str | BLEDevice) -> bool:
    """Check whether the scale uses the new-style characteristics."""
    try:
        async with BleakClient(address_or_ble_device) as client:
            characteristics = [
                char.uuid for char in client.services.characteristics.values()
            ]
    except BleakDeviceNotFoundError as ex:
        raise AcaiaDeviceNotFound("Device not found") from ex
    except (BleakError, TimeoutError) as ex:
        raise AcaiaError(ex) from ex

    if OLD_STYLE_CHAR_ID in characteristics:
        return False
    if DEFAULT_CHAR_ID in characteristics:
        return True
    raise AcaiaUnknownDevice


def derive_model_name(name: str | None) -> str | None:
    """Derive the marketing model name from the advertised device name."""
    if name is None:
        return None
    if name == "PROCHBT001":
        return "Pearl"
    if "-" not in name:
        return None
    prefix = name.split("-")[0]
    if prefix in ("PEARL", "LUNAR", "PYXIS"):
        return prefix.capitalize()
    if prefix == "ACAIAL":
        return "Lunar"
    return None
