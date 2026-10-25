import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.ndvi import ClearReadingOut


class CropOut(BaseModel):
    crop_cycle_id: uuid.UUID
    # The Knowledge Base crop the rules come from, when the crop has one.
    crop_id: str | None
    # The crop as typed in AgTrail.
    crop: str
    planted_date: date
    days_since_planting: int
    status: str


class CropHealthBlock(BaseModel):
    # fresh | stale | no_clear_reading | no_data
    status: str
    # Seen by the satellite, not worked out.
    provenance: str = "observed"
    latest_clear: ClearReadingOut | None
    previous_clear: ClearReadingOut | None
    passes_last_30_days: int
    unusable_passes_last_30_days: int


class AttentionItem(BaseModel):
    operation_id: uuid.UUID
    operation_date: date
    crop: str
    kb_operation_id: str | None
    # timing | order | repeat | other
    kind: str
    summary: str


class PhaseSummary(BaseModel):
    phase: str
    name: str
    operations: int
    flagged: int
    warnings: int
    # Not judged: the step could not be identified, or the crop has no such step.
    unknown: int


class SignalsBlock(BaseModel):
    # Worked out from what was logged and the crop rules, not seen on the farm.
    provenance: str = "inferred"
    kb_version: str | None
    # ok | warning | flagged | not_applicable | unknown, how many operations of each
    counts: dict[str, int]
    # The same operations grouped by phase, in order. Empty when the phases could not be looked up.
    by_phase: list[PhaseSummary] = []
    # Jobs that were not logged. A gap in the records, not necessarily a mistake.
    record_gaps: int
    # Real problems with what was done (too early or late, wrong order, too often), newest first.
    needs_attention: list[AttentionItem]


class PriceBlock(BaseModel):
    crop_id: str
    # priced | unpriced | unavailable
    status: str
    pis_commodity: str | None = None
    price_ngn_per_kg: float | None = None
    # observed: a real recent price. estimated: carried forward from the last real one.
    provenance: str | None = None
    last_real_price_date: date | None = None
    last_real_price_source: str | None = None
    last_real_price_age_days: int | None = None
    note: str | None = None
    # PIS's expected change in price over 30 days. A model figure, so always an estimate.
    outlook_30d_pct: float | None = None
    outlook_30d_signal: str | None = None
    outlook_provenance: str = "estimated"


class WeatherDayOut(BaseModel):
    date: str
    condition: str
    high_c: float
    low_c: float
    rain_mm: float


class WeatherBlock(BaseModel):
    # ok | no_forecast_for_state | unavailable
    status: str
    # A forecast, so an estimate.
    provenance: str = "estimated"
    state: str | None = None
    city: str | None = None
    issued_at: str | None = None
    source: str | None = None
    # Required by the data licence whenever the forecast is shown.
    attribution: str | None = None
    days: list[WeatherDayOut] = []
    note: str | None = None


class FarmIntelligenceOut(BaseModel):
    farm_id: uuid.UUID
    as_of: date
    generated_at: datetime
    crops: list[CropOut]
    crop_health: CropHealthBlock
    signals: SignalsBlock
    prices: list[PriceBlock]
    weather: WeatherBlock
