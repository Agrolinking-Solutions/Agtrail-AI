"""
What the satellite can honestly say about a farm right now: the latest usable
reading, how old it is, and how often the farm has been hidden by cloud lately.
It reports facts and never a verdict. Judging whether the crop is healthy needs
the crop and its stage, which is not decided here.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from app.models.ndvi import FarmNdviScene

# A pass counts as a usable reading only if at least this much of the farm was
# clear. Below it, too few pixels are left to stand for the whole farm.
MIN_CLEAR_FRACTION = 0.5
# Sentinel-2 passes about every 5 days, and in the rains about half are
# cloudy, so a reading up to two weeks old is still current.
FRESH_WITHIN_DAYS = 14
LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class ClearReading:
    scene_date: date
    ndvi_mean: float
    clear_fraction: float
    polygon_pixels: int
    age_days: int


@dataclass(frozen=True)
class CropHealthSummary:
    status: str  # fresh | stale | no_clear_reading | no_data
    as_of: date
    latest_clear: ClearReading | None
    previous_clear: ClearReading | None
    passes_last_30_days: int
    unusable_passes_last_30_days: int


def _usable(scene: FarmNdviScene) -> bool:
    return scene.ndvi_mean is not None and scene.clear_fraction >= MIN_CLEAR_FRACTION


def summarize(scenes: list[FarmNdviScene], today: date) -> CropHealthSummary:
    """scenes: every stored pass for one farm, any order."""
    ordered = sorted((s for s in scenes if s.scene_date <= today), key=lambda s: s.scene_date, reverse=True)

    def reading(scene: FarmNdviScene) -> ClearReading:
        return ClearReading(
            scene_date=scene.scene_date,
            ndvi_mean=scene.ndvi_mean,
            clear_fraction=scene.clear_fraction,
            polygon_pixels=scene.polygon_pixels,
            age_days=(today - scene.scene_date).days,
        )

    usable = [s for s in ordered if _usable(s)]
    recent = [s for s in ordered if (today - s.scene_date).days < LOOKBACK_DAYS]
    latest = reading(usable[0]) if usable else None
    previous = reading(usable[1]) if len(usable) > 1 else None

    if not ordered:
        status = "no_data"
    elif latest is None:
        status = "no_clear_reading"
    else:
        status = "fresh" if latest.age_days <= FRESH_WITHIN_DAYS else "stale"

    return CropHealthSummary(
        status=status,
        as_of=today,
        latest_clear=latest,
        previous_clear=previous,
        passes_last_30_days=len(recent),
        unusable_passes_last_30_days=sum(1 for s in recent if not _usable(s)),
    )
