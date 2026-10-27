import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict


class NdviObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period_start: date
    period_end: date
    ndvi_mean: float
    valid_pixel_count: int


class ClearReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scene_date: date
    ndvi_mean: float
    clear_fraction: float
    polygon_pixels: int
    age_days: int


class CropHealthOut(BaseModel):
    farm_id: uuid.UUID
    # fresh | stale | no_clear_reading | no_data
    status: str
    # How the figure was obtained, in the hub's provenance vocabulary.
    provenance: str = "observed"
    as_of: date
    latest_clear: ClearReadingOut | None
    previous_clear: ClearReadingOut | None
    passes_last_30_days: int
    unusable_passes_last_30_days: int
    min_clear_fraction: float
    fresh_within_days: int


class NdviFrameOut(BaseModel):
    scene_date: date
    ndvi_mean: float | None
    clear_fraction: float
    # Where to fetch the picture (a PNG with transparent cloud pixels), on this API.
    image_path: str


class NdviFramesOut(BaseModel):
    farm_id: uuid.UUID
    # Seen by the satellite, not worked out.
    provenance: str = "observed"
    # Every frame covers exactly this box, [west, south, east, north] in longitude and latitude, and is
    # stretched to a square picture to fit it. Null when the farm has no boundary.
    bbox: list[float] | None = None
    # The same box as four corners in the order a map image layer takes them:
    # top-left, top-right, bottom-right, bottom-left, each [longitude, latitude].
    corners: list[list[float]] | None = None
    count: int
    # Oldest first, ready to play as a timelapse. Only passes where at least half the farm was clear.
    frames: list[NdviFrameOut]
