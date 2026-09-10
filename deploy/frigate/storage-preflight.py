#!/usr/bin/env python3
"""Fail container startup when Frigate storage mounts are unsafe."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _unescape_mountinfo(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)


@dataclass(frozen=True)
class MountInfo:
    mount_point: str
    fstype: str
    source: str


def _mounts() -> list[MountInfo]:
    mounts: list[MountInfo] = []

    with open("/proc/self/mountinfo") as f:
        for line in f:
            before, _, after = line.partition(" - ")
            pre_fields = before.split()
            post_fields = after.split()

            if len(pre_fields) < 5 or len(post_fields) < 2:
                continue

            mounts.append(
                MountInfo(
                    mount_point=_unescape_mountinfo(pre_fields[4]),
                    fstype=post_fields[0],
                    source=_unescape_mountinfo(post_fields[1]),
                )
            )

    return mounts


def _mount_for(path: str) -> MountInfo | None:
    path = os.path.abspath(path)
    best: MountInfo | None = None

    for mount in _mounts():
        mount_point = mount.mount_point.rstrip("/") or "/"
        if path == mount_point or path.startswith(f"{mount_point}/"):
            if best is None or len(mount_point) > len(best.mount_point):
                best = mount

    return best


def _fail(message: str) -> None:
    print(f"storage preflight failed: {message}", file=sys.stderr)
    sys.exit(1)


def _check_path(
    path: str,
    *,
    description: str,
    required_fstype: str | None = None,
    required_source: str | None = None,
    min_total_gb: int = 0,
    min_free_gb: int = 0,
) -> None:
    if not os.path.isdir(path):
        _fail(f"{description} path does not exist or is not a directory: {path}")

    if not os.access(path, os.W_OK):
        _fail(f"{description} path is not writable: {path}")

    mount = _mount_for(path)
    if mount is None:
        _fail(f"{description} path has no mount entry: {path}")

    if required_fstype and mount.fstype != required_fstype:
        _fail(
            f"{description} path {path} is {mount.fstype} from {mount.source}, "
            f"expected fstype {required_fstype}"
        )

    if required_source and required_source not in mount.source:
        _fail(
            f"{description} path {path} is from {mount.source}, "
            f"expected source containing {required_source}"
        )

    usage = os.statvfs(path)
    total_gb = usage.f_frsize * usage.f_blocks // 1024**3
    free_gb = usage.f_frsize * usage.f_bavail // 1024**3

    if min_total_gb and total_gb < min_total_gb:
        _fail(
            f"{description} path {path} total is {total_gb}GB, "
            f"expected at least {min_total_gb}GB"
        )

    if min_free_gb and free_gb < min_free_gb:
        _fail(
            f"{description} path {path} free is {free_gb}GB, "
            f"expected at least {min_free_gb}GB"
        )


def main() -> int:
    archive_path = os.environ.get("FRIGATE_ARCHIVE_TIER_ROOT", "/media/frigate")
    hot_path = os.environ.get("FRIGATE_HOT_TIER_ROOT", "/mnt/local/recordings")

    _check_path(
        archive_path,
        description="archive tier",
        required_fstype=os.environ.get("FRIGATE_ARCHIVE_TIER_REQUIRED_FSTYPE", "nfs4"),
        required_source=os.environ.get(
            "FRIGATE_ARCHIVE_TIER_REQUIRED_SOURCE",
            "10.10.10.3:/tank/frigate_archive",
        ),
        min_total_gb=_env_int("FRIGATE_ARCHIVE_TIER_MIN_TOTAL_GB", 1000),
        min_free_gb=_env_int("FRIGATE_ARCHIVE_TIER_MIN_FREE_GB", 100),
    )
    _check_path(
        hot_path,
        description="hot tier",
        min_total_gb=_env_int("FRIGATE_HOT_TIER_MIN_TOTAL_GB", 200),
        min_free_gb=_env_int("FRIGATE_HOT_TIER_MIN_FREE_GB", 20),
    )

    if len(sys.argv) < 2:
        _fail("no command provided")

    os.execvp(sys.argv[1], sys.argv[1:])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
