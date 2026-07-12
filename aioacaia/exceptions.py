"""Exceptions for aioacaia."""

from bleak.exc import BleakDeviceNotFoundError, BleakError


class AcaiaScaleException(Exception):
    """Base class for exceptions in this module."""


class AcaiaDeviceNotFound(BleakDeviceNotFoundError, AcaiaScaleException):
    """Exception when no device is found."""


class AcaiaError(BleakError, AcaiaScaleException):
    """Exception for general bleak errors."""


class AcaiaUnknownDevice(AcaiaScaleException):
    """Exception for unknown devices."""


class AcaiaMessageError(AcaiaScaleException):
    """Exception for message errors."""

    def __init__(self, bytes_recvd: bytearray, message: str) -> None:
        super().__init__(message)
        self.message = message
        self.bytes_recvd = bytes_recvd


class AcaiaMessageTooShort(AcaiaMessageError):
    """Exception for messages that are too short."""

    def __init__(self, bytes_recvd: bytearray) -> None:
        super().__init__(bytes_recvd, "Message too short")


class AcaiaMessageTooLong(AcaiaMessageError):
    """Exception for messages that are too long."""

    def __init__(self, bytes_recvd: bytearray) -> None:
        super().__init__(bytes_recvd, "Message too long")
