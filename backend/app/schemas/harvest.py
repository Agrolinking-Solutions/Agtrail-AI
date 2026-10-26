from datetime import date
from typing import Optional

from pydantic import BaseModel


class PerHectareOut(BaseModel):
    typical: float
    low: float
    high: float


class AmountOut(BaseModel):
    area_ha: float
    # season for a crop harvested each season, year for a tree crop that bears every year
    per: str
    # what the tonnes are of: grain, paddy (unmilled), fresh roots, dry beans...
    yield_basis: str
    typical_tonnes: float
    low_tonnes: float
    high_tonnes: float
    per_hectare: PerHectareOut


class TimingOut(BaseModel):
    counted_from: str
    counted_from_date: date
    earliest: date
    latest: date
    # before_window | in_window | after_window
    state: str
    days_to_earliest: int
    days_to_latest: int


class ValueOut(BaseModel):
    currency: str = "NGN"
    typical: float
    low: float
    high: float
    price_ngn_per_kg: float
    # observed or estimated: how real the price is, as for /intelligence prices
    price_provenance: str | None
    price_last_real_date: date | None
    price_age_days: int | None


class SourceOut(BaseModel):
    title: str
    publisher: str | None = None
    url: str | None = None
    figure: str
    year: int


class SoilOut(BaseModel):
    # What AgTrail recorded for the farm, in its own words
    recorded: Optional[str] = None
    # preferred | avoid | not_listed | unknown
    status: str
    texture: Optional[str] = None
    preferred: list[str] = []
    note: Optional[str] = None
    # none | high_capped_at_typical: whether the soil changed the range
    effect: str = "none"


class HarvestEstimateOut(BaseModel):
    # ok | no_estimate
    status: str
    # no_crop | no_cycle | no_research | no_area | estimate_unavailable, when there is no estimate
    reason_code: str | None = None
    reason: str | None = None
    # Always an estimate: the typical result for the crop on a farm this size, not a forecast for this farm.
    provenance: str = "estimated"
    crop_id: str | None = None
    crop: str | None = None
    as_of: str | None = None
    confidence: float | None = None
    amount: AmountOut | None = None
    timing: TimingOut | None = None
    soil: SoilOut | None = None
    timing_note: str | None = None
    value: ValueOut | None = None
    value_note: str | None = None
    assumptions: list[str] = []
    caveats: list[str] = []
    method: str | None = None
    sources: list[SourceOut] = []


class FarmHarvestEstimateOut(HarvestEstimateOut):
    farm_id: str
