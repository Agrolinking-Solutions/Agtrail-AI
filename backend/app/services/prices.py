"""
The price of a crop, with an honest account of how real it is.

The Knowledge Base says which PIS commodity prices each crop. PIS says the price.
What the hub adds is the label. PIS reports a price "today" even when the last
real observation is weeks old and has been carried forward, so a price is only
called observed when PIS says today's figure is not an estimate. Otherwise it is
estimated, and the date and source of the last real price travel with it.

Nothing is invented: a crop with no PIS series is "unpriced" with no number, and
if either service cannot be reached the answer says so.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol

from app.clients.base import ClientResult
from app.contracts.knowledge_base import CommodityMapping
from app.contracts.pis import CommodityForecast


class KnowledgeBase(Protocol):
    def commodities(self) -> ClientResult[CommodityMapping]: ...


class Pis(Protocol):
    def forecast(self, commodity: str) -> ClientResult[CommodityForecast]: ...


@dataclass(frozen=True)
class CropPrice:
    crop_id: str
    # priced | unpriced | unavailable
    status: str
    pis_commodity: Optional[str] = None
    # Naira per kilogram, as PIS reports it today.
    price_ngn_per_kg: Optional[float] = None
    # observed: PIS says today's figure is a real price. estimated: it is the last
    # real price carried forward, or a model figure.
    provenance: Optional[str] = None
    last_real_price_date: Optional[date] = None
    last_real_price_source: Optional[str] = None
    last_real_price_age_days: Optional[int] = None
    # The reason there is no number, or what to be careful about.
    note: Optional[str] = None
    # The KB's own words about the mapping (units, conversion).
    mapping_note: Optional[str] = None
    # Multiply a yield in tonnes by this and by the price to get a value. None when it has not been sourced.
    conversion_factor: Optional[float] = None


def _date(value: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def crop_price(crop_id: str, kb: KnowledgeBase, pis: Pis, today: Optional[date] = None) -> CropPrice:
    today = today or date.today()

    mapping = kb.commodities()
    if not mapping.ok:
        return CropPrice(crop_id, "unavailable", note=f"Knowledge Base unavailable: {mapping.unavailable_reason}")
    row = mapping.value.for_crop(crop_id)
    if row is None:
        return CropPrice(crop_id, "unpriced", note="This crop is not in the price mapping.")
    if row.price_status != "priced" or not row.pis_commodity:
        return CropPrice(crop_id, "unpriced", mapping_note=row.conversion_note, note="PIS has no price series for this crop yet.")

    forecast = pis.forecast(row.pis_commodity)
    if not forecast.ok:
        return CropPrice(
            crop_id, "unavailable", pis_commodity=row.pis_commodity, mapping_note=row.conversion_note,
            note=f"PIS unavailable: {forecast.unavailable_reason}",
        )

    value = forecast.value
    real = value.latest_real_price
    real_date = _date(real.date) if real else None
    observed = not value.start_is_estimate
    note = None
    if forecast.stale:
        note = f"PIS could not be reached just now; this is the last answer received ({forecast.unavailable_reason})."
    elif not observed:
        note = "Today's price is the last real price carried forward, not a fresh observation."

    return CropPrice(
        crop_id=crop_id,
        status="priced",
        pis_commodity=row.pis_commodity,
        price_ngn_per_kg=value.today_price_per_unit if value.unit_label.lower().endswith("/kg") else None,
        provenance="observed" if observed else "estimated",
        last_real_price_date=real_date,
        last_real_price_source=real.source if real else None,
        last_real_price_age_days=(today - real_date).days if real_date else None,
        note=note if value.unit_label.lower().endswith("/kg") else f"PIS quotes this in {value.unit_label}, not per kilogram.",
        mapping_note=row.conversion_note,
        conversion_factor=row.conversion_factor,
    )
