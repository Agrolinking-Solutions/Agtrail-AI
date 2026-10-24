"""
One farm, one summary: what the satellite sees, what the crop rules say about the
operations logged, the crop's price and its 30-day outlook, and the weather.

Every block says how it was obtained (observed, inferred or estimated) and when.
Nothing is filled in to look complete: a block with nothing to say says so, and a
service that cannot be reached shows up as unavailable inside its own block, never
as an error for the whole summary.
"""

from datetime import date, datetime, timezone
from typing import Optional

from app.models.crop_cycle import CropCycle
from app.models.farm_operation import FarmOperation
from app.models.ndvi import FarmNdviScene
from app.models.operation_signal import OperationSignal
from app.contracts.knowledge_base import RuleIssue
from app.schemas.intelligence import (
    AttentionItem,
    CropHealthBlock,
    CropOut,
    FarmIntelligenceOut,
    PriceBlock,
    SignalsBlock,
    WeatherBlock,
    WeatherDayOut,
)
from app.services import crop_health
from app.services.prices import crop_price
from app.services.signal_engine import classify_issue

EARLIEST = datetime.min.replace(tzinfo=timezone.utc)
MAX_CROPS = 5
MAX_PRICED_CROPS = 3
MAX_ATTENTION = 5
# Problems with what was done, as opposed to a job that simply was not logged.
REAL_PROBLEMS = {"timing", "order", "repeat", "other"}


def _crops(cycles: list[CropCycle], today: date) -> list[CropOut]:
    ordered = sorted(cycles, key=lambda c: c.planted_date, reverse=True)[:MAX_CROPS]
    return [
        CropOut(
            crop_cycle_id=c.id, crop_id=c.crop_id, crop=c.crop_raw, planted_date=c.planted_date,
            days_since_planting=(today - c.planted_date).days, status=c.status,
        )
        for c in ordered
    ]


def _crop_health(scenes: list[FarmNdviScene], today: date) -> CropHealthBlock:
    summary = crop_health.summarize(scenes, today)
    return CropHealthBlock(
        status=summary.status, latest_clear=summary.latest_clear, previous_clear=summary.previous_clear,
        passes_last_30_days=summary.passes_last_30_days, unusable_passes_last_30_days=summary.unusable_passes_last_30_days,
    )


def _signals(rows: list[tuple[OperationSignal, FarmOperation, CropCycle]]) -> SignalsBlock:
    counts: dict[str, int] = {}
    gaps = 0
    attention: list[AttentionItem] = []
    for signal, operation, cycle in sorted(rows, key=lambda r: (r[1].operation_date, r[1].created_at or EARLIEST), reverse=True):
        status = "flagged" if signal.status == "blocked" else signal.status
        counts[status] = counts.get(status, 0) + 1
        seen = set()
        for raw in signal.issues:
            kind, summary = classify_issue(RuleIssue(**raw))
            if kind == "missing_step":
                gaps += 1
            elif kind in REAL_PROBLEMS and kind not in seen and len(attention) < MAX_ATTENTION:
                # Several rules can object to the same operation in the same way; the
                # first (the hardest rule) speaks for them and /signals has the rest.
                seen.add(kind)
                attention.append(AttentionItem(
                    operation_id=operation.id, operation_date=operation.operation_date, crop=cycle.crop_raw,
                    kb_operation_id=signal.kb_operation_id, kind=kind, summary=summary or raw["message"],
                ))
    versions = [r[0].kb_version for r in rows if r[0].kb_version]
    return SignalsBlock(kb_version=versions[0] if versions else None, counts=counts, record_gaps=gaps, needs_attention=attention)


def _prices(cycles: list[CropCycle], kb, pis, today: date) -> list[PriceBlock]:
    crop_ids = []
    for cycle in sorted(cycles, key=lambda c: c.planted_date, reverse=True):
        if cycle.crop_id and cycle.crop_id not in crop_ids:
            crop_ids.append(cycle.crop_id)
    outlook = pis.outlook()
    blocks = []
    for crop_id in crop_ids[:MAX_PRICED_CROPS]:
        price = crop_price(crop_id, kb, pis, today)
        block = PriceBlock(
            crop_id=crop_id, status=price.status, pis_commodity=price.pis_commodity,
            price_ngn_per_kg=price.price_ngn_per_kg, provenance=price.provenance,
            last_real_price_date=price.last_real_price_date, last_real_price_source=price.last_real_price_source,
            last_real_price_age_days=price.last_real_price_age_days, note=price.note,
        )
        if price.status == "priced" and outlook.ok:
            entry = outlook.value.per_commodity.get(price.pis_commodity)
            if entry is not None:
                block.outlook_30d_pct, block.outlook_30d_signal = entry.pct_change_30d, entry.signal
        blocks.append(block)
    return blocks


def _weather(state: Optional[str], pis) -> WeatherBlock:
    result = pis.weather()
    if not result.ok:
        return WeatherBlock(status="unavailable", state=state, note=f"PIS unavailable: {result.unavailable_reason}")
    place = result.value.for_state(state) if state else None
    if place is None:
        return WeatherBlock(
            status="no_forecast_for_state", state=state,
            note="PIS has no forecast for this state yet." if state else "The farm's state is not known.",
        )
    return WeatherBlock(
        status="ok", state=place.state, city=place.city, issued_at=result.value.issued_at,
        source=result.value.source, attribution=result.value.attribution,
        days=[WeatherDayOut(date=d.date, condition=d.condition, high_c=d.high_c, low_c=d.low_c, rain_mm=d.rain_mm) for d in place.days],
        note="Forecast for the main city of the farm's state, not the farm itself."
        + (" Last answer received; PIS could not be reached just now." if result.stale else ""),
    )


def build_intelligence(
    farm_id,
    state: Optional[str],
    cycles: list[CropCycle],
    scenes: list[FarmNdviScene],
    signal_rows: list[tuple[OperationSignal, FarmOperation, CropCycle]],
    kb,
    pis,
    today: Optional[date] = None,
) -> FarmIntelligenceOut:
    now = datetime.now(timezone.utc)
    today = today or now.date()
    return FarmIntelligenceOut(
        farm_id=farm_id,
        as_of=today,
        generated_at=now,
        crops=_crops(cycles, today),
        crop_health=_crop_health(scenes, today),
        signals=_signals(signal_rows),
        prices=_prices(cycles, kb, pis, today),
        weather=_weather(state, pis),
    )
