"""Telethon bridge package for the future MTProto transport service."""

try:
    from apps.telethon_bridge.client import TelethonBridgeClient
    from apps.telethon_bridge.errors import (
        PeerResolutionError,
        SessionNotAuthorizedError,
        TelethonBridgeError,
    )
    from apps.telethon_bridge.index_sync import sync_all_indexes, sync_chat_index, sync_topic_index
    from apps.telethon_bridge.service import TelethonBridgeService
except ModuleNotFoundError:  # pragma: no cover - optional dependency at runtime
    TelethonBridgeClient = None
    TelethonBridgeService = None
    TelethonBridgeError = RuntimeError
    SessionNotAuthorizedError = RuntimeError
    PeerResolutionError = RuntimeError
    sync_all_indexes = None
    sync_chat_index = None
    sync_topic_index = None

__all__ = [
    "PeerResolutionError",
    "SessionNotAuthorizedError",
    "TelethonBridgeClient",
    "TelethonBridgeError",
    "TelethonBridgeService",
    "sync_all_indexes",
    "sync_chat_index",
    "sync_topic_index",
]
