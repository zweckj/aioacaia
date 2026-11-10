"""Tests for aioacaia.discovery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aioacaia.discovery import derive_model_name, scan


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (None, None),
        ("PROCHBT001", "Pearl"),
        ("PEARL-244684", "Pearl"),
        ("LUNAR-123456", "Lunar"),
        ("PYXIS-123456", "Pyxis"),
        ("ACAIAL-123456", "Lunar"),
        ("BARW27074A2", "Baratza Sette 270Wi"),
        ("UNKNOWN-123", None),
        ("UNKNOWN", None),
    ],
)
def test_derive_model_name(name: str | None, expected: str | None) -> None:
    """Advertised names map to the expected marketing model name."""
    assert derive_model_name(name) == expected


async def test_scan_finds_baratza_sette() -> None:
    """The Sette 270Wi is picked up by discovery, unrelated devices are not."""
    scanner = SimpleNamespace(
        discover=AsyncMock(
            return_value=[
                SimpleNamespace(name="BARW27074A2", address="00:1C:97:17:74:A2"),
                SimpleNamespace(name="LUNAR-123456", address="aa:bb:cc:dd:ee:ff"),
                SimpleNamespace(name="Headphones", address="11:22:33:44:55:66"),
                SimpleNamespace(name=None, address="77:88:99:aa:bb:cc"),
            ]
        )
    )
    assert await scan(scanner, timeout=1) == [
        "00:1C:97:17:74:A2",
        "aa:bb:cc:dd:ee:ff",
    ]
