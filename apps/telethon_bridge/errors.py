"""Telethon bridge specific errors."""


class TelethonBridgeError(RuntimeError):
    """Base error for Telethon bridge failures."""


class SessionNotAuthorizedError(TelethonBridgeError):
    """Raised when the Telethon session is missing or not authorized."""


class PeerResolutionError(TelethonBridgeError):
    """Raised when a peer reference cannot be resolved safely."""
