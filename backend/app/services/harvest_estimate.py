"""
A harvest estimate for one crop on one farm, as a range and always labelled an estimate.

How much: the farm's size times the crop's typical and normal yields per hectare. When: the
planting (or transplanting) date plus the expected harvest window. Worth: the tonnes times the
crop's price, only when the price is known and the unit conversion is sourced.

The crop's numbers and their sources come from the Knowledge Base, with the date they were
researched. This is the typical result for a farm of this crop and size, not a forecast for this
farm: it knows nothing about this season's rain, soil, seed or care. Anything it cannot work out
is left out with the reason, never filled in.
"""

from datetime import date, timedelta
from typing import Optional

from app.schemas.harvest import AmountOut, HarvestEstimateOut, PerHectareOut, SourceOut, TimingOut, ValueOut
from app.services.prices import crop_price


def _none(code: str, reason: str, crop_id: Optional[str] = None, crop: Optional[str] = None) -> HarvestEstimateOut:
    return HarvestEstimateOut(status="no_estimate", reason_code=code, reason=reason, crop_id=crop_id, crop=crop)


def _round_naira(value: float) -> float:
    return float(round(value, -3))


def _timing(window, planted_on: Optional[date], transplanted_on: Optional[date], today: date):
    if window is None:
        return None, "This crop bears every year, so there is no single harvest date."
    reference = transplanted_on if window.after == "transplanting" else planted_on
    if reference is None:
        what = "transplanting" if window.after == "transplanting" else "planting"
        return None, f"The {what} date is not recorded, so the harvest time cannot be worked out."
    earliest, latest = reference + timedelta(days=window.min_days), reference + timedelta(days=window.max_days)
    state = "before_window" if today < earliest else ("in_window" if today <= latest else "after_window")
    return TimingOut(
        counted_from=window.after, counted_from_date=reference, earliest=earliest, latest=latest, state=state,
        days_to_earliest=(earliest - today).days, days_to_latest=(latest - today).days,
    ), None


def _value(crop_id: str, basis, amount: AmountOut, kb, pis, today: date):
    price = crop_price(crop_id, kb, pis, today)
    if price.status == "unpriced":
        return None, "PIS has no price series for this crop yet, so no naira value is shown."
    if price.status != "priced" or price.price_ngn_per_kg is None:
        return None, price.note or "The price is not available just now."
    if price.conversion_factor is None:
        return None, (f"The tonnes are {basis.yield_basis} and the price is for a different product. "
                      "The conversion has not been sourced yet, so no naira value is shown.")
    per_tonne = price.price_ngn_per_kg * 1000 * price.conversion_factor
    return ValueOut(
        typical=_round_naira(amount.typical_tonnes * per_tonne), low=_round_naira(amount.low_tonnes * per_tonne),
        high=_round_naira(amount.high_tonnes * per_tonne), price_ngn_per_kg=price.price_ngn_per_kg,
        price_provenance=price.provenance, price_last_real_date=price.last_real_price_date,
        price_age_days=price.last_real_price_age_days,
    ), (price.note if price.provenance == "estimated" else None)


def build_estimate(
    *,
    crop_id: Optional[str],
    crop_name: Optional[str],
    area_ha: Optional[float],
    planted_on: Optional[date],
    transplanted_on: Optional[date],
    kb,
    pis,
    today: Optional[date] = None,
) -> HarvestEstimateOut:
    today = today or date.today()
    if not crop_id:
        return _none("no_crop", "The crop is not one the Knowledge Base has rules for yet.", None, crop_name)

    result = kb.harvest_estimate(crop_id)
    if not result.ok:
        return _none("estimate_unavailable", f"Knowledge Base unavailable: {result.unavailable_reason}", crop_id, crop_name)
    basis = result.value
    if not basis.available or basis.yield_t_per_ha is None:
        return _none("no_research", basis.reason or "No current research is recorded for this crop yet.", crop_id, crop_name)
    if not area_ha or area_ha <= 0:
        return _none("no_area", "The farm's size is not known, so the amount cannot be worked out.", crop_id, crop_name)

    y = basis.yield_t_per_ha
    amount = AmountOut(
        area_ha=round(area_ha, 4), per=basis.per or "season", yield_basis=basis.yield_basis or "",
        typical_tonnes=round(area_ha * y.typical, 2), low_tonnes=round(area_ha * y.normal_low, 2),
        high_tonnes=round(area_ha * y.normal_high, 2),
        per_hectare=PerHectareOut(typical=y.typical, low=y.normal_low, high=y.normal_high),
    )
    timing, timing_note = _timing(basis.harvest_window, planted_on, transplanted_on, today)
    value, value_note = _value(crop_id, basis, amount, kb, pis, today)

    return HarvestEstimateOut(
        status="ok", crop_id=crop_id, crop=crop_name, as_of=basis.as_of, confidence=basis.confidence,
        amount=amount, timing=timing, timing_note=timing_note, value=value, value_note=value_note,
        assumptions=[f"Assumes {crop_name or crop_id} covers the whole recorded farm size ({round(area_ha, 2)} ha)."],
        caveats=basis.caveats, method=basis.method,
        sources=[SourceOut(title=s.title, publisher=s.publisher, url=s.url, figure=s.figure, year=s.year) for s in basis.sources],
    )
