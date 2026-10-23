import json
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_api_key
from app.models.farm import Farm
from app.models.farm_operation import FarmOperation
from app.models.ndvi import FarmNdviObservation, FarmNdviScene
from app.models.operation_signal import OperationSignal
from app.schemas.location import FarmLocationOut
from app.schemas.ndvi import CropHealthOut, NdviObservationOut
from app.schemas.signals import FarmSignalsOut, OperationSignalOut, RuleIssueOut
from app.services import crop_health
from app.services.mapbox import fetch_area_view, fetch_boundary_satellite_image
from app.services.sentinel import IMAGE_STYLES, fetch_farm_image, get_access_token

router = APIRouter(
    prefix="/farms",
    tags=["farms"],
    dependencies=[Depends(require_api_key)],
)

# In-process cache keyed by (farm_id, period_start, style): a Copernicus
# render costs processing quota, and the same farm/period/style is
# rendered on every page view until the underlying imagery changes,
# which is at most daily. Per-worker only, each uvicorn worker has its
# own copy, that is fine here. Always keyed by our own Farm.id, even when
# the caller looked the farm up by AgTrail's id, so both ids share one
# cache entry instead of rendering (and paying for) the same image twice.
_IMAGE_CACHE: dict[tuple[uuid.UUID, str, str], tuple[float, bytes]] = {}
_IMAGE_CACHE_TTL_SECONDS = 6 * 60 * 60


def resolve_farm(db: Session, farm_id: uuid.UUID) -> Farm | None:
    """
    AgTrail's frontend only ever has AgTrail's own farm id, not the one we
    generate when the farm.created webhook creates the row, so {farm_id}
    has to accept either. Our own id is checked first since it's the
    primary key lookup; agtrail_farm_id is a plain unique column.
    """
    farm = db.get(Farm, farm_id)
    if farm is not None:
        return farm
    return db.execute(
        select(Farm).where(Farm.agtrail_farm_id == str(farm_id))
    ).scalar_one_or_none()


@router.get("/{farm_id}/location", response_model=FarmLocationOut)
def get_farm_location(farm_id: uuid.UUID, db: Session = Depends(get_db)):
    """
    Where the farm is, for a map that flies or pans to it: a center point
    to navigate to, and the mapped boundary (if any) to draw as an
    outline. This is plain location data for a basemap like Google Maps
    or Mapbox, a different thing from the satellite imagery endpoints
    above, AgTrail's frontend does the actual map rendering and the
    fly-to animation with it.
    """
    farm = resolve_farm(db, farm_id)
    if farm is None:
        raise HTTPException(status_code=404, detail="Farm not found")

    # coordinates comes from AgTrail's farm.created event, which in
    # practice is rarely populated; the boundary's own centroid is a
    # reliable fallback for any farm that's actually been mapped.
    center = func.coalesce(Farm.coordinates, func.ST_Centroid(Farm.boundary))
    row = db.execute(
        select(
            Farm.id,
            Farm.boundary_mapped,
            func.ST_Y(center),
            func.ST_X(center),
            func.ST_AsGeoJSON(Farm.boundary),
        ).where(Farm.id == farm.id)
    ).first()

    farm_id_, boundary_mapped, lat, lng, boundary_geojson = row
    return FarmLocationOut(
        farm_id=farm_id_,
        latitude=lat,
        longitude=lng,
        boundary_mapped=boundary_mapped,
        boundary=json.loads(boundary_geojson) if boundary_geojson else None,
    )


@router.get("/{farm_id}/ndvi", response_model=list[NdviObservationOut])
def get_ndvi_trend(
    farm_id: uuid.UUID,
    limit: int = Query(default=52, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Oldest to newest, so a chart can plot it left to right without resorting."""
    farm = resolve_farm(db, farm_id)
    if farm is None:
        raise HTTPException(status_code=404, detail="Farm not found")

    observations = db.execute(
        select(FarmNdviObservation)
        .where(FarmNdviObservation.farm_id == farm.id)
        .order_by(FarmNdviObservation.period_start.desc())
        .limit(limit)
    ).scalars().all()

    return list(reversed(observations))


@router.get("/{farm_id}/crop-health", response_model=CropHealthOut)
def get_crop_health(farm_id: uuid.UUID, db: Session = Depends(get_db)):
    """
    The latest usable satellite reading for the farm and how many days old it
    is, with the one before it and how many recent passes were hidden by cloud.
    Facts only: status is "fresh", "stale" (reading older than two weeks),
    "no_clear_reading" (the farm was seen but always under cloud) or "no_data"
    (no pass stored). A reading is never invented or carried forward.
    """
    farm = resolve_farm(db, farm_id)
    if farm is None:
        raise HTTPException(status_code=404, detail="Farm not found")

    scenes = db.execute(select(FarmNdviScene).where(FarmNdviScene.farm_id == farm.id)).scalars().all()
    summary = crop_health.summarize(scenes, datetime.now(timezone.utc).date())
    return CropHealthOut(
        farm_id=farm.id,
        status=summary.status,
        as_of=summary.as_of,
        latest_clear=summary.latest_clear,
        previous_clear=summary.previous_clear,
        passes_last_30_days=summary.passes_last_30_days,
        unusable_passes_last_30_days=summary.unusable_passes_last_30_days,
        min_clear_fraction=crop_health.MIN_CLEAR_FRACTION,
        fresh_within_days=crop_health.FRESH_WITHIN_DAYS,
    )


@router.get("/{farm_id}/signals", response_model=FarmSignalsOut)
def get_farm_signals(farm_id: uuid.UUID, db: Session = Depends(get_db)):
    """
    What the crop rules say about each operation logged on the farm, newest first.
    status is "ok", "warning", "flagged" (a rule is broken), "not_applicable" (the
    crop has no such step) or "unknown" (not judged, and reason says why). An
    operation is only judged when it can be identified for certain. These are
    inferred from the logged data, not seen on the farm, and are rebuilt every time
    the judging job runs.
    """
    farm = resolve_farm(db, farm_id)
    if farm is None:
        raise HTTPException(status_code=404, detail="Farm not found")

    rows = db.execute(
        select(OperationSignal, FarmOperation)
        .join(FarmOperation, FarmOperation.id == OperationSignal.operation_id)
        .where(OperationSignal.farm_id == farm.id)
        .order_by(FarmOperation.operation_date.desc(), FarmOperation.created_at.desc())
    ).all()

    signals = [
        OperationSignalOut(
            operation_id=operation.id,
            operation_date=operation.operation_date,
            logged_type=operation.logged_type_raw,
            kb_operation_id=signal.kb_operation_id,
            status="flagged" if signal.status == "blocked" else signal.status,
            reason=signal.reason,
            issues=[RuleIssueOut(**issue) for issue in signal.issues],
            checked_at=signal.checked_at,
        )
        for signal, operation in rows
    ]
    counts: dict[str, int] = {}
    for item in signals:
        counts[item.status] = counts.get(item.status, 0) + 1
    versions = [signal.kb_version for signal, _ in rows if signal.kb_version]
    return FarmSignalsOut(farm_id=farm.id, kb_version=versions[0] if versions else None, counts=counts, signals=signals)


@router.get("/{farm_id}/ndvi-image")
def get_ndvi_image(
    farm_id: uuid.UUID,
    style: str = Query(default="ndvi", pattern="^(ndvi|true-color|mapbox|mapbox-area)$"),
    db: Session = Depends(get_db),
):
    """
    A satellite image of the farm. Four styles: "ndvi" colors brown for
    bare soil to green for dense vegetation; "true-color" is the
    ordinary RGB view from Sentinel-2; "mapbox" is high-resolution
    commercial imagery (Maxar, via Mapbox), tightly framed on the farm
    itself, for a visibly sharper look at the same plot; "mapbox-area"
    is a wider, fixed-zoom street map around the farm's location, for
    "where is this plot and what's nearby" rather than "what does the
    plot itself look like."

    "ndvi" and "true-color" cover the same window as the farm's latest
    NDVI observation, so they agree with the trend chart on what period
    they're showing. The two mapbox styles have no time window, just
    the current imagery, so neither needs an NDVI observation to exist
    yet, only a mapped boundary.
    """
    farm = resolve_farm(db, farm_id)
    if farm is None:
        raise HTTPException(status_code=404, detail="Farm not found")

    if style in ("mapbox", "mapbox-area"):
        if not farm.boundary_mapped or farm.boundary is None:
            raise HTTPException(status_code=404, detail="Farm has no mapped boundary yet")

        cache_key = (farm.id, "current", style)
        cached = _IMAGE_CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < _IMAGE_CACHE_TTL_SECONDS:
            return Response(content=cached[1], media_type="image/png")

        if style == "mapbox":
            boundary_geojson = json.loads(
                db.execute(select(func.ST_AsGeoJSON(Farm.boundary)).where(Farm.id == farm.id)).scalar_one()
            )
            png_bytes = fetch_boundary_satellite_image(boundary_geojson)
        else:
            centroid = func.ST_Centroid(Farm.boundary)
            lng, lat, boundary_geojson = db.execute(
                select(func.ST_X(centroid), func.ST_Y(centroid), func.ST_AsGeoJSON(Farm.boundary))
                .where(Farm.id == farm.id)
            ).one()
            png_bytes = fetch_area_view(lng, lat, json.loads(boundary_geojson))

        _IMAGE_CACHE[cache_key] = (time.monotonic(), png_bytes)
        return Response(content=png_bytes, media_type="image/png")

    latest = db.execute(
        select(FarmNdviObservation)
        .where(FarmNdviObservation.farm_id == farm.id)
        .order_by(FarmNdviObservation.period_start.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest is None:
        raise HTTPException(status_code=404, detail="No NDVI data for this farm yet")

    cache_key = (farm.id, latest.period_start.isoformat(), style)
    cached = _IMAGE_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < _IMAGE_CACHE_TTL_SECONDS:
        return Response(content=cached[1], media_type="image/png")

    boundary_geojson = json.loads(
        db.execute(select(func.ST_AsGeoJSON(Farm.boundary)).where(Farm.id == farm.id)).scalar_one()
    )
    token = get_access_token()
    png_bytes = fetch_farm_image(IMAGE_STYLES[style], boundary_geojson, latest.period_start, latest.period_end, token)
    _IMAGE_CACHE[cache_key] = (time.monotonic(), png_bytes)

    return Response(content=png_bytes, media_type="image/png")
