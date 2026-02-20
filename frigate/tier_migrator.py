"""Migrate recording segments between storage tiers."""

import datetime
import logging
import os
import shutil
import threading
from pathlib import Path

from frigate.config import FrigateConfig
from frigate.models import Recordings

logger = logging.getLogger(__name__)

MIGRATION_BATCH_SIZE = 100


class StorageTierMigrator(threading.Thread):
    """Migrate aged-out recording segments from hot tier to cold tier(s)."""

    def __init__(self, config: FrigateConfig, stop_event) -> None:
        super().__init__(name="storage_tier_migrator")
        self.config = config
        self.stop_event = stop_event
        self.daemon = True

    def _is_tier_available(self, path: str) -> bool:
        """Check if a tier's path is mounted and writable."""
        try:
            if not os.path.isdir(path):
                return False
            shutil.disk_usage(path)
            return os.access(path, os.W_OK)
        except (OSError, PermissionError) as e:
            logger.warning(f"Storage tier {path} is unavailable: {e}")
            return False

    def _migrate_segment(
        self, recording, source_tier_path: str, dest_tier_path: str
    ) -> bool:
        """Migrate a single recording segment from source to dest tier.

        Atomic: copy -> verify size -> update DB -> delete source.
        Returns True on success, False on failure.
        """
        source_path = recording.path

        if not os.path.exists(source_path):
            logger.debug(f"Source file missing, skipping: {source_path}")
            return False

        if not source_path.startswith(source_tier_path):
            logger.warning(
                f"Recording path {source_path} does not start with "
                f"expected tier {source_tier_path}, skipping"
            )
            return False

        relative_path = source_path[len(source_tier_path) :].lstrip("/")
        dest_path = os.path.join(dest_tier_path, relative_path)
        dest_dir = os.path.dirname(dest_path)
        temp_dest = dest_path + ".tmp"

        try:
            os.makedirs(dest_dir, exist_ok=True)

            source_size = os.path.getsize(source_path)
            shutil.copy2(source_path, temp_dest)

            dest_size = os.path.getsize(temp_dest)
            if source_size != dest_size:
                logger.error(
                    f"Size mismatch after copy: source={source_size}, "
                    f"dest={dest_size} for {source_path}"
                )
                Path(temp_dest).unlink(missing_ok=True)
                return False

            os.rename(temp_dest, dest_path)

            Recordings.update(path=dest_path).where(
                Recordings.id == recording.id
            ).execute()

            Path(source_path).unlink(missing_ok=True)

            logger.debug(f"Migrated {source_path} -> {dest_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to migrate {source_path}: {e}")
            Path(temp_dest).unlink(missing_ok=True)
            return False

    def _run_migration_cycle(self) -> None:
        """Run one migration cycle across all tier boundaries."""
        tiers = self.config.storage.tiers
        if len(tiers) < 2:
            return

        now = datetime.datetime.now().timestamp()

        for i in range(len(tiers) - 1):
            source_tier = tiers[i]
            dest_tier = tiers[i + 1]

            if source_tier.max_age_hours is None:
                continue

            if not self._is_tier_available(dest_tier.path):
                logger.warning(
                    f"Destination tier {dest_tier.path} unavailable, "
                    f"skipping migration cycle for tier {i} -> {i + 1}"
                )
                continue

            cutoff = now - (source_tier.max_age_hours * 3600)

            eligible = (
                Recordings.select(
                    Recordings.id,
                    Recordings.path,
                    Recordings.start_time,
                    Recordings.end_time,
                    Recordings.segment_size,
                )
                .where(
                    Recordings.path.startswith(source_tier.path),
                    Recordings.end_time < cutoff,
                )
                .order_by(Recordings.start_time.asc())
                .limit(MIGRATION_BATCH_SIZE)
                .namedtuples()
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

            if migrated > 0 or failed > 0:
                logger.info(
                    f"Tier migration {source_tier.path} -> {dest_tier.path}: "
                    f"migrated={migrated}, failed={failed}"
                )

    def run(self) -> None:
        """Main thread loop."""
        interval = self.config.storage.migration_interval
        logger.info(
            f"Storage tier migrator started with "
            f"{len(self.config.storage.tiers)} tiers, interval={interval}s"
        )

        # Initial delay to let recordings stabilize after startup
        if self.stop_event.wait(60):
            return

        while not self.stop_event.wait(interval):
            try:
                self._run_migration_cycle()
            except Exception as e:
                logger.error(f"Error in storage tier migration cycle: {e}")

        logger.info("Exiting storage tier migrator...")
