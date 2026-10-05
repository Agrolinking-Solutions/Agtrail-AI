from datetime import date

from sqlalchemy import select

from tests.conftest import requires_db
from app.services.sentinel import NdviPeriod, parse_statistics_response


def _interval(start: str, end: str, mean, samples) -> dict:
    return {
        "interval": {"from": f"{start}T00:00:00Z", "to": f"{end}T00:00:00Z"},
        "outputs": {
            "ndvi": {
                "bands": {
                    "B0": {"stats": {"mean": mean, "sampleCount": samples, "noDataCount": 0}}
                }
            }
        },
    }


def test_parse_keeps_intervals_with_valid_pixels():
    payload = {"data": [_interval("2026-09-01", "2026-09-11", 0.61, 18)]}

    periods = parse_statistics_response(payload)

    assert periods == [
        NdviPeriod(
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 11),
            ndvi_mean=0.61,
            valid_pixel_count=18,
        )
    ]


def test_parse_skips_intervals_with_no_valid_pixels():
    payload = {
        "data": [
            _interval("2026-09-01", "2026-09-11", None, 0),
            _interval("2026-09-11", "2026-09-21", 0.4, 0),
            _interval("2026-09-21", "2026-10-01", 0.55, 12),
        ]
    }

    periods = parse_statistics_response(payload)

    assert [p.period_start for p in periods] == [date(2026, 9, 21)]


def test_parse_handles_empty_response():
    assert parse_statistics_response({"data": []}) == []


@requires_db
def test_upsert_is_idempotent_and_updates_existing_period(db_session):
    from app.jobs.ingest_ndvi import _upsert_periods
    from app.models.farm import Farm
    from app.models.farmer import Farmer
    from app.models.ndvi import FarmNdviObservation

    farmer = Farmer(full_name="Test Farmer", phone="+2340000000000", state="Kano", lga="Kano Municipal")
    db_session.add(farmer)
    db_session.flush()
    farm = Farm(farmer_id=farmer.id, name="Test Farm", boundary_mapped=True)
    db_session.add(farm)
    db_session.flush()

    first = NdviPeriod(date(2026, 9, 1), date(2026, 9, 11), 0.5, 10)
    revised = NdviPeriod(date(2026, 9, 1), date(2026, 9, 11), 0.6, 14)

    _upsert_periods(db_session, farm, [first])
    _upsert_periods(db_session, farm, [revised])
    db_session.commit()

    rows = db_session.execute(
        select(FarmNdviObservation).where(FarmNdviObservation.farm_id == farm.id)
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].ndvi_mean == 0.6
    assert rows[0].valid_pixel_count == 14
