from typing import Optional

from pydantic import Field, field_validator

from .base import FrigateBaseModel

__all__ = ["StorageConfig", "StorageTierConfig"]


class StorageTierConfig(FrigateBaseModel):
    path: str = Field(title="Filesystem path for this storage tier.")
    max_age_hours: Optional[int] = Field(
        default=None,
        title=(
            "Maximum age in hours before recordings migrate to the next tier. "
            "Only the final tier may omit this."
        ),
    )


class StorageConfig(FrigateBaseModel):
    tiers: list[StorageTierConfig] = Field(
        default_factory=list,
        title=(
            "Ordered recording storage tiers. The first tier is hot storage; "
            "later tiers are progressively colder."
        ),
    )
    migration_interval: int = Field(
        default=300,
        title="Seconds between tier migration cycles.",
    )
    migration_timeout: int = Field(
        default=120,
        title="Seconds allowed for a single tier migration filesystem operation.",
    )

    @field_validator("tiers")
    @classmethod
    def validate_tiers(
        cls, tiers: list[StorageTierConfig]
    ) -> list[StorageTierConfig]:
        if not tiers:
            return tiers

        if tiers[-1].max_age_hours is not None:
            raise ValueError(
                "The last storage tier must have max_age_hours set to None."
            )

        for index, tier in enumerate(tiers[:-1]):
            if tier.max_age_hours is None:
                raise ValueError(
                    f"Storage tier {index} must have max_age_hours set."
                )

        paths = [tier.path.rstrip("/") for tier in tiers]
        if len(paths) != len(set(paths)):
            raise ValueError("Storage tier paths must be unique.")

        for tier, path in zip(tiers, paths):
            tier.path = path

        return tiers
