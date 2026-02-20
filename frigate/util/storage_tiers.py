"""Utilities for resolving storage tier paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

from frigate.const import RECORD_DIR

if TYPE_CHECKING:
    from frigate.config import FrigateConfig


def get_all_recording_dirs(config: FrigateConfig) -> list[str]:
    """Return all recording directories from the storage tier config.

    If no tiers are configured, returns [RECORD_DIR] for backward compat.
    """
    if config.storage.tiers:
        return [tier.path for tier in config.storage.tiers]
    return [RECORD_DIR]


def get_hot_tier_path(config: FrigateConfig) -> str:
    """Return the hot (first) tier path where new recordings land.

    If no tiers configured, returns RECORD_DIR.
    """
    if config.storage.tiers:
        return config.storage.tiers[0].path
    return RECORD_DIR
