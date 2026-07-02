"""Migrate recording segments between storage tiers."""

from __future__ import annotations

import argparse
import datetime
import json
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
DEFAULT_STATUS_FILE = "/config/tier_migrator_status.json"
DEFAULT_HEALTH_MAX_AGE_SECONDS = 1800
DEFAULT_MIN_FREE_GB = 80
DEFAULT_TARGET_FREE_GB = 120
DEFAULT_MAX_USAGE_PERCENT = 75


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        logger.warning("Invalid integer for %s, using default %s", name, default)
        return default


def _atomic_write_json(path: str, data: dict) -> None:
    temp_path = f"{path}.tmp"
    with open(temp_path, "w") as f:
        json.dump(data, f, sort_keys=True)
    os.replace(temp_path, path)


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

        # Force NFS close-to-open attribute revalidation before checking size.
        with open(temp_dest, "rb"):
            pass

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
        self.status_file = os.environ.get(
            "FRIGATE_TIER_STATUS_FILE", DEFAULT_STATUS_FILE
        )
        self.min_free_bytes = (
            _env_int("FRIGATE_HOT_TIER_MIN_FREE_GB", DEFAULT_MIN_FREE_GB)
            * 1024**3
        )
        self.target_free_bytes = (
            _env_int("FRIGATE_HOT_TIER_TARGET_FREE_GB", DEFAULT_TARGET_FREE_GB)
            * 1024**3
        )
        self.max_usage_percent = _env_int(
            "FRIGATE_HOT_TIER_MAX_USAGE_PERCENT", DEFAULT_MAX_USAGE_PERCENT
        )

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

    def _status_payload(
        self,
        state: str,
        source_tier_path: str | None = None,
        dest_tier_path: str | None = None,
        eligible: int = 0,
        migrated: int = 0,
        failed: int = 0,
        pressure_mode: bool = False,
        error: str | None = None,
    ) -> dict:
        payload = {
            "state": state,
            "time": time.time(),
            "time_iso": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source_tier": source_tier_path,
            "dest_tier": dest_tier_path,
            "eligible": eligible,
            "migrated": migrated,
            "failed": failed,
            "pressure_mode": pressure_mode,
            "error": error,
        }

        tiers = self.config.storage.tiers
        if tiers:
            try:
                usage = shutil.disk_usage(tiers[0].path)
                payload["hot_tier"] = {
                    "path": tiers[0].path,
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "usage_percent": round((usage.used / usage.total) * 100, 2),
                    "min_free": self.min_free_bytes,
                    "target_free": self.target_free_bytes,
                    "max_usage_percent": self.max_usage_percent,
                }
            except Exception as e:
                payload["hot_tier"] = {
                    "path": tiers[0].path,
                    "error": str(e),
                }

        return payload

    def _write_status(self, payload: dict) -> None:
        try:
            _atomic_write_json(self.status_file, payload)
        except Exception:
            logger.exception("Failed to write tier migrator status file")

    def _hot_tier_pressure_active(self, source_tier_path: str) -> tuple[bool, dict]:
        usage = shutil.disk_usage(source_tier_path)
        usage_percent = (usage.used / usage.total) * 100
        active = (
            usage.free < self.min_free_bytes
            or usage_percent > self.max_usage_percent
        )
        return active, {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "usage_percent": round(usage_percent, 2),
        }

    def _pressure_target_reached(self, source_tier_path: str) -> bool:
        usage = shutil.disk_usage(source_tier_path)
        usage_percent = (usage.used / usage.total) * 100
        return (
            usage.free >= self.target_free_bytes
            and usage_percent <= self.max_usage_percent
        )

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

    def _run_migration_cycle(self) -> dict:
        tiers = self.config.storage.tiers
        if len(tiers) < 2:
            payload = self._status_payload("no_tiers")
            self._write_status(payload)
            return payload

        now = datetime.datetime.now().timestamp()
        cycle_migrated = 0
        cycle_failed = 0
        cycle_eligible = 0
        pressure_mode_used = False

        for index in range(len(tiers) - 1):
            source_tier = tiers[index]
            dest_tier = tiers[index + 1]

            if source_tier.max_age_hours is None:
                continue

            pressure_active = False
            if index == 0:
                try:
                    pressure_active, pressure_usage = self._hot_tier_pressure_active(
                        source_tier.path
                    )
                    if pressure_active:
                        pressure_mode_used = True
                        logger.warning(
                            "Hot tier pressure active for %s: free=%s usage=%s%%",
                            source_tier.path,
                            pressure_usage["free"],
                            pressure_usage["usage_percent"],
                        )
                except Exception:
                    logger.exception("Failed to check hot tier pressure")

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
            if pressure_active:
                cutoff = now - 3600

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
            cycle_eligible += len(eligible)

            if eligible:
                logger.info(
                    f"Tier migration {source_tier.path} -> {dest_tier.path}: "
                    f"starting batch count={len(eligible)} cutoff={cutoff} "
                    f"pressure={pressure_active}"
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

                if (migrated + failed) % 100 == 0:
                    self._write_status(
                        self._status_payload(
                            "running",
                            source_tier_path=source_tier.path,
                            dest_tier_path=dest_tier.path,
                            eligible=len(eligible),
                            migrated=migrated,
                            failed=failed,
                            pressure_mode=pressure_active,
                        )
                    )

                if pressure_active and self._pressure_target_reached(source_tier.path):
                    logger.info(
                        "Hot tier pressure target reached for %s", source_tier.path
                    )
                    break

            if migrated > 0 or failed > 0:
                logger.info(
                    f"Tier migration {source_tier.path} -> {dest_tier.path}: "
                    f"migrated={migrated}, failed={failed}"
                )
            cycle_migrated += migrated
            cycle_failed += failed

        payload = self._status_payload(
            "ok",
            eligible=cycle_eligible,
            migrated=cycle_migrated,
            failed=cycle_failed,
            pressure_mode=pressure_mode_used,
        )
        self._write_status(payload)
        return payload

    def run(self) -> None:
        interval = self.config.storage.migration_interval
        logger.info(
            "Storage tier migrator started with "
            f"{len(self.config.storage.tiers)} tiers, interval={interval}s"
        )
        self._write_status(self._status_payload("starting"))

        if self.stop_event.wait(60):
            return

        while not self.stop_event.is_set():
            try:
                self._run_migration_cycle()
            except Exception as e:
                logger.exception("Error in storage tier migration cycle")
                self._write_status(self._status_payload("error", error=str(e)))

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


def healthcheck_status(status_file: str, max_age_seconds: int) -> int:
    try:
        with open(status_file) as f:
            status = json.load(f)
    except Exception as e:
        print(f"tier migrator status unreadable: {e}", file=sys.stderr)
        return 1

    status_time = float(status.get("time", 0))
    age = time.time() - status_time
    state = status.get("state")

    if state == "error":
        print(f"tier migrator status is error: {status.get('error')}", file=sys.stderr)
        return 1

    if age > max_age_seconds:
        print(
            f"tier migrator status is stale: age={age:.0f}s "
            f"max={max_age_seconds}s state={state}",
            file=sys.stderr,
        )
        return 1

    print(
        "tier migrator healthy: "
        f"state={state} age={age:.0f}s migrated={status.get('migrated')}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Frigate tiered storage migrator.")
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument("--run-once", action="store_true")
    parser.add_argument(
        "--status-file",
        default=os.environ.get("FRIGATE_TIER_STATUS_FILE", DEFAULT_STATUS_FILE),
    )
    parser.add_argument(
        "--health-max-age-seconds",
        type=int,
        default=_env_int(
            "FRIGATE_TIER_HEALTH_MAX_AGE_SECONDS",
            DEFAULT_HEALTH_MAX_AGE_SECONDS,
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    )

    if args.healthcheck:
        return healthcheck_status(args.status_file, args.health_max_age_seconds)

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
