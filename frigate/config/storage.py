"""Storage tier configuration."""

from typing import Optional

from pydantic import Field, field_validator

from .base import FrigateBaseModel

__all__ = ["StorageConfig", "StorageTierConfig"]


class StorageTierConfig(FrigateBaseModel):
    path: str = Field(title="Filesystem path for this storage tier.")
    max_age_hours: Optional[float] = Field(
        default=None,
        ge=0,
        title="Maximum age in hours before segments are migrated to the next tier. None means segments stay here indefinitely.",
    )


class StorageConfig(FrigateBaseModel):
    tiers: list[StorageTierConfig] = Field(
        default_factory=list,
        title="Ordered list of storage tiers. First tier is hot (fast), subsequent tiers are progressively colder. If empty, single-tier behavior using RECORD_DIR is used.",
    )
    migration_interval: int = Field(
        default=300,
        ge=60,
        title="Seconds between tier migration cycles.",
    )

    @field_validator("tiers")
    @classmethod
    def validate_tiers(
        cls, v: list[StorageTierConfig],
    ) -> list[StorageTierConfig]:
        if len(v) > 0:
            # Last tier must have max_age_hours=None (final resting place)
            if v[-1].max_age_hours is not None:
                raise ValueError(
                    "The last storage tier must have max_age_hours set to None "
                    "(it is the final tier)."
                )
            # All non-last tiers must have max_age_hours set
            for i, tier in enumerate(v[:-1]):
                if tier.max_age_hours is None:
                    raise ValueError(
                        f"Storage tier {i} must have max_age_hours set "
                        "(only the last tier can omit it)."
                    )
            # Paths must be unique
            paths = [tier.path for tier in v]
            if len(paths) != len(set(paths)):
                raise ValueError("Storage tier paths must be unique.")
        return v
