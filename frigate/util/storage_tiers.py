"""Utilities for resolving recording paths across storage tiers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from frigate.const import RECORD_DIR

if TYPE_CHECKING:
    from frigate.config import FrigateConfig


def get_all_recording_dirs(config: FrigateConfig) -> list[str]:
    if config.storage.tiers:
        return [tier.path for tier in config.storage.tiers]

    return [RECORD_DIR]


def get_hot_tier_path(config: FrigateConfig) -> str:
    if config.storage.tiers:
        return config.storage.tiers[0].path

    return RECORD_DIR


def get_tier_relative_path(path: str, tier_path: str) -> str | None:
    tier_path = tier_path.rstrip("/")
    if path == tier_path:
        return ""
    if path.startswith(f"{tier_path}/"):
        return path[len(tier_path) + 1 :]
    return None


def resolve_recording_path(config: FrigateConfig, path: str) -> str:
    """Return an existing path for a recording, checking alternate tiers.

    The DB stores the current physical path. During interrupted migrations that
    value can be stale, so playback paths use this resolver as a narrow fallback
    instead of scattering tier checks through the API.
    """
    if os.path.exists(path):
        return path

    tiers = get_all_recording_dirs(config)
    if len(tiers) < 2:
        return path

    relative_path = None
    for tier_path in tiers:
        relative_path = get_tier_relative_path(path, tier_path)
        if relative_path is not None:
            break

    if relative_path is None:
        return path

    for tier_path in tiers:
        candidate = os.path.join(tier_path, relative_path)
        if candidate != path and os.path.exists(candidate):
            return candidate

    return path


def clear_recording_path(config: FrigateConfig, path: str, missing_ok: bool = True) -> None:
    resolved_path = resolve_recording_path(config, path)
    Path(resolved_path).unlink(missing_ok=missing_ok)
