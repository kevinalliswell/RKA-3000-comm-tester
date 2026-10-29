class BalanceSerialError(Exception):
    """Base exception for the balance_serial package."""


class SerialConnectionError(BalanceSerialError):
    """Raised when the serial port cannot be opened or used."""


class UnsupportedProtocolError(BalanceSerialError):
    """Raised when a requested balance protocol is not registered."""
