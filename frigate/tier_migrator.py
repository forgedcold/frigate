"""Migrate recording segments between storage tiers."""

from __future__ import annotations

import argparse
import datetime
import logging
import multiprocessing
import os
import queue
import shutil
import signal
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from playhouse.sqliteq import SqliteQueueDatabase

from frigate.config import FrigateConfig
from frigate.models import Recordings

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

MIGRATION_BATCH_SIZE = 1000
MIGRATION_TIMEOUT_BACKOFF = 60
MIGRATION_PROCESS_SHUTDOWN_TIMEOUT = 1


def dest_path_for_tier(
    source_path: str, source_tier_path: str, dest_tier_path: str
) -> str:
    relative_path = source_path[len(source_tier_path) :].lstrip("/")
    return os.path.join(dest_tier_path, relative_path)


def _check_tier_path(path: str, result_queue) -> None:
    try:
        result_queue.put(
            {
                "ok": os.path.isdir(path)
                and shutil.disk_usage(path)
                and os.access(path, os.W_OK)
            }
        )
    except Exception as e:
        result_queue.put({"ok": False, "error": str(e)})


def _copy_segment_to_tier(
    source_path: str, source_tier_path: str, dest_tier_path: str, result_queue
) -> None:
    temp_dest = None

    try:
        if not os.path.exists(source_path):
            result_queue.put({"ok": False, "error": "source file missing"})
            return

        if not source_path.startswith(f"{source_tier_path}/"):
            result_queue.put(
                {
                    "ok": False,
                    "error": (
                        "recording path does not start with expected tier "
                        f"{source_tier_path}"
                    ),
                }
            )
            return

        dest_path = dest_path_for_tier(source_path, source_tier_path, dest_tier_path)
        temp_dest = f"{dest_path}.tmp"

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        source_size = os.path.getsize(source_path)
        shutil.copy2(source_path, temp_dest)

        dest_size = os.path.getsize(temp_dest)
        if source_size != dest_size:
            Path(temp_dest).unlink(missing_ok=True)
            result_queue.put(
                {
                    "ok": False,
                    "error": (
                        f"size mismatch after copy: source={source_size}, "
                        f"dest={dest_size}"
                    ),
                }
            )
            return

        os.rename(temp_dest, dest_path)
        result_queue.put({"ok": True, "dest_path": dest_path})

    except Exception as e:
        cleanup_error = None
        if temp_dest is not None:
            try:
                Path(temp_dest).unlink(missing_ok=True)
            except Exception as cleanup_e:
                cleanup_error = cleanup_e

        error = str(e)
        if cleanup_error is not None:
            error = f"{error}; failed to clean temp file: {cleanup_error}"

        result_queue.put({"ok": False, "error": error})


class StorageTierMigrator(threading.Thread):
    """Migrate aged-out recording segments from hot tier to colder tiers."""

    def __init__(self, config: FrigateConfig, stop_event: threading.Event) -> None:
        super().__init__(name="storage_tier_migrator")
        self.config = config
        self.stop_event = stop_event
        self.daemon = True
        self._mp_context = multiprocessing.get_context("fork")
        self._tier_backoff_until: dict[str, float] = {}

    @property
    def _migration_timeout(self) -> int:
        return self.config.storage.migration_timeout

    def _run_filesystem_task(
        self,
        target: "Callable",
        args: tuple,
        timeout: float,
        description: str,
    ) -> tuple[bool, dict, bool]:
        result_queue = self._mp_context.Queue(maxsize=1)
        process = self._mp_context.Process(target=target, args=(*args, result_queue))
        process.start()
        process.join(timeout)

        if process.is_alive():
            logger.error(
                f"{description} exceeded {timeout}s, terminating helper process"
            )
            process.terminate()
            process.join(MIGRATION_PROCESS_SHUTDOWN_TIMEOUT)

            if process.is_alive():
                process.kill()
                process.join(MIGRATION_PROCESS_SHUTDOWN_TIMEOUT)

            if process.is_alive():
                logger.error(
                    f"{description} helper process did not exit; it may be stuck "
                    "in kernel I/O until the mount recovers"
                )
            else:
                process.close()

            result_queue.close()
            return False, {}, True

        try:
            result = result_queue.get(timeout=MIGRATION_PROCESS_SHUTDOWN_TIMEOUT)
        except queue.Empty:
            result = {
                "ok": False,
                "error": f"helper exited with code {process.exitcode} without result",
            }

        process.close()
        result_queue.close()
        return bool(result.get("ok")), result, False

    def _backoff_tier(self, path: str, reason: str) -> None:
        self._tier_backoff_until[path] = time.monotonic() + MIGRATION_TIMEOUT_BACKOFF
        logger.error(
            f"Backing off storage tier {path} for {MIGRATION_TIMEOUT_BACKOFF}s: "
            f"{reason}"
        )

    def _is_tier_in_backoff(self, path: str) -> bool:
        return time.monotonic() < self._tier_backoff_until.get(path, 0)

    def _is_tier_available(self, path: str) -> bool:
        if self._is_tier_in_backoff(path):
            return False

        available, result, timed_out = self._run_filesystem_task(
            _check_tier_path,
            (path,),
            self._migration_timeout,
            f"Storage tier availability check for {path}",
        )

        if timed_out:
            self._backoff_tier(path, "availability check timed out")
            return False

        if not available:
            logger.warning(
                f"Storage tier {path} is unavailable: "
                f"{result.get('error', 'not writable or not mounted')}"
            )

        return available

    def _migrate_segment(
        self, recording, source_tier_path: str, dest_tier_path: str
    ) -> bool:
        source_path = recording.path

        if not os.path.exists(source_path):
            dest_path = dest_path_for_tier(
                source_path, source_tier_path, dest_tier_path
            )
            if os.path.exists(dest_path):
                Recordings.update(path=dest_path).where(
                    Recordings.id == recording.id
                ).execute()
                logger.info(
                    "Reconciled stale recording row to existing archive path: "
                    f"{source_path} -> {dest_path}"
                )
            else:
                Recordings.delete().where(Recordings.id == recording.id).execute()
                logger.warning(
                    "Deleted stale recording row with missing hot and archive files: "
                    f"{source_path}"
                )
            return True

        if not source_path.startswith(f"{source_tier_path}/"):
            logger.warning(
                f"Recording path {source_path} does not start with expected tier "
                f"{source_tier_path}, skipping"
            )
            return False

        copied, result, timed_out = self._run_filesystem_task(
            _copy_segment_to_tier,
            (source_path, source_tier_path, dest_tier_path),
            self._migration_timeout,
            f"Tier migration copy for {source_path}",
        )

        if timed_out:
            self._backoff_tier(dest_tier_path, "migration copy timed out")
            return False

        if not copied:
            logger.error(
                f"Failed to migrate {source_path}: "
                f"{result.get('error', 'unknown error')}"
            )
            return False

        dest_path = result["dest_path"]
        Recordings.update(path=dest_path).where(Recordings.id == recording.id).execute()
        Path(source_path).unlink(missing_ok=True)

        logger.debug(f"Migrated {source_path} -> {dest_path}")
        return True

    def _run_migration_cycle(self) -> None:
        tiers = self.config.storage.tiers
        if len(tiers) < 2:
            return

        now = datetime.datetime.now().timestamp()

        for index in range(len(tiers) - 1):
            source_tier = tiers[index]
            dest_tier = tiers[index + 1]

            if source_tier.max_age_hours is None:
                continue

            if self._is_tier_in_backoff(dest_tier.path):
                logger.warning(
                    f"Destination tier {dest_tier.path} is in timeout backoff, "
                    f"skipping migration cycle for tier {index} -> {index + 1}"
                )
                continue

            if not self._is_tier_available(dest_tier.path):
                logger.warning(
                    f"Destination tier {dest_tier.path} unavailable, "
                    f"skipping migration cycle for tier {index} -> {index + 1}"
                )
                continue

            cutoff = now - (source_tier.max_age_hours * 3600)
            eligible = list(
                Recordings.select(
                    Recordings.id,
                    Recordings.path,
                    Recordings.start_time,
                    Recordings.end_time,
                    Recordings.segment_size,
                )
                .where(
                    Recordings.path.startswith(f"{source_tier.path}/"),
                    Recordings.end_time < cutoff,
                )
                .order_by(Recordings.start_time.asc())
                .limit(MIGRATION_BATCH_SIZE)
                .namedtuples()
            )

            if eligible:
                logger.info(
                    f"Tier migration {source_tier.path} -> {dest_tier.path}: "
                    f"starting batch count={len(eligible)} cutoff={cutoff}"
                )

            migrated = 0
            failed = 0
            for recording in eligible:
                if self.stop_event.is_set():
                    return
                if self._migrate_segment(recording, source_tier.path, dest_tier.path):
                    migrated += 1
                else:
                    failed += 1
                    if self._is_tier_in_backoff(dest_tier.path):
                        break

            if migrated > 0 or failed > 0:
                logger.info(
                    f"Tier migration {source_tier.path} -> {dest_tier.path}: "
                    f"migrated={migrated}, failed={failed}"
                )

    def run(self) -> None:
        interval = self.config.storage.migration_interval
        logger.info(
            "Storage tier migrator started with "
            f"{len(self.config.storage.tiers)} tiers, interval={interval}s"
        )

        if self.stop_event.wait(60):
            return

        while not self.stop_event.is_set():
            try:
                self._run_migration_cycle()
            except Exception:
                logger.exception("Error in storage tier migration cycle")

            if self.stop_event.wait(interval):
                break

        logger.info("Exiting storage tier migrator")


def bind_database(config: FrigateConfig) -> SqliteQueueDatabase:
    db = SqliteQueueDatabase(
        config.database.path,
        pragmas={
            "auto_vacuum": "FULL",
            "cache_size": -512 * 1000,
            "synchronous": "NORMAL",
        },
        timeout=60,
    )
    db.bind([Recordings])
    return db


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Frigate tiered storage migrator.")
    parser.add_argument("--run-once", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    )

    config = FrigateConfig.load(install=False)
    if len(config.storage.tiers) < 2:
        logger.info("No tiered storage configured; exiting")
        return 0

    db = bind_database(config)
    stop_event = threading.Event()

    def stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    migrator = StorageTierMigrator(config, stop_event)
    if args.run_once:
        migrator._run_migration_cycle()
        db.stop()
        return 0

    migrator.start()
    while migrator.is_alive() and not stop_event.is_set():
        migrator.join(timeout=1)

    stop_event.set()
    migrator.join(timeout=10)
    db.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
