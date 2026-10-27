import json
import time
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_api_key
from app.models.farm import Farm
from app.models.crop_cycle import CropCycle
from app.models.farm_operation import FarmOperation
from app.models.ndvi import FarmNdviFrame, FarmNdviObservation, FarmNdviScene
from app.models.operation_signal import OperationSignal
from app.schemas.location import FarmLocationOut
from app.schemas.harvest import FarmHarvestEstimateOut
from app.schemas.intelligence import FarmIntelligenceOut
from app.schemas.ndvi import CropHealthOut, NdviFrameOut, NdviFramesOut, NdviObservationOut
from app.schemas.signals import FarmSignalsOut, OperationSignalOut, RuleIssueOut
from app.contracts.knowledge_base import RuleIssue
from app.clients.knowledge_base import get_knowledge_base_client
from app.clients.pis import get_pis_client
from app.models.farmer import Farmer
from app.services import crop_health
from app.services.farm_intelligence import build_intelligence
from app.services.harvest_estimate import build_estimate
from app.services.phases import phase_of
from app.services.signal_engine import classify_issue
from app.services.mapbox import fetch_area_view, fetch_boundary_satellite_image
from app.services.ndvi_frames import read_png
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


def knowledge_base():
    return get_knowledge_base_client()


def pis():
    return get_pis_client()


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
def get_farm_signals(farm_id: uuid.UUID, db: Session = Depends(get_db), kb=Depends(knowledge_base)):
    """
    What the crop rules say about each operation logged on the farm, newest first.
    status is "ok", "warning", "flagged" (a rule is broken), "not_applicable" (the
    crop has no such step) or "unknown" (not judged, and reason says why). An
    operation is only judged when it can be identified for certain. These are
    inferred from the logged data, not seen on the farm, and are rebuilt every time
    the judging job runs. Each issue has a kind: "missing_step" means an earlier job
    was never logged (a gap in the records), "timing", "order" and "repeat" are about
    what was done, and the Knowledge Base's own wording is always kept in message.
    """
    farm = resolve_farm(db, farm_id)
    if farm is None:
        raise HTTPException(status_code=404, detail="Farm not found")

    taxonomy_result = kb.taxonomy()
    taxonomy = taxonomy_result.value if taxonomy_result.ok else None

    rows = db.execute(
        select(OperationSignal, FarmOperation, CropCycle)
        .join(FarmOperation, FarmOperation.id == OperationSignal.operation_id)
        .join(CropCycle, CropCycle.id == OperationSignal.crop_cycle_id)
        .where(OperationSignal.farm_id == farm.id)
        .order_by(FarmOperation.operation_date.desc(), FarmOperation.created_at.desc())
    ).all()

    def issue_out(raw: dict) -> RuleIssueOut:
        kind, summary = classify_issue(RuleIssue(**raw))
        return RuleIssueOut(**raw, kind=kind, summary=summary)

    def phase_out(signal, operation, cycle):
        return phase_of(
            taxonomy, signal_category=operation.signal_category, kb_operation_id=signal.kb_operation_id,
            logged_type=operation.logged_type_raw, crop_id=cycle.crop_id,
        )

    signals = [
        OperationSignalOut(
            operation_id=operation.id,
            crop_cycle_id=cycle.id,
            crop_id=cycle.crop_id,
            crop=cycle.crop_raw,
            phase=phase_out(signal, operation, cycle),
            phase_name=taxonomy.phase_name(phase_out(signal, operation, cycle)) if taxonomy else None,
            operation_date=operation.operation_date,
            logged_type=operation.logged_type_raw,
            kb_operation_id=signal.kb_operation_id,
            status="flagged" if signal.status == "blocked" else signal.status,
            reason=signal.reason,
            issues=[issue_out(issue) for issue in signal.issues],
            checked_at=signal.checked_at,
        )
        for signal, operation, cycle in rows
    ]
    counts: dict[str, int] = {}
    issue_kinds: dict[str, int] = {}
    phase_counts: dict[str, int] = {}
    for item in signals:
        counts[item.status] = counts.get(item.status, 0) + 1
        phase_counts[item.phase or "none"] = phase_counts.get(item.phase or "none", 0) + 1
        for issue in item.issues:
            issue_kinds[issue.kind] = issue_kinds.get(issue.kind, 0) + 1
    versions = [row[0].kb_version for row in rows if row[0].kb_version]
    return FarmSignalsOut(
        farm_id=farm.id, kb_version=versions[0] if versions else None, counts=counts, phases_available=taxonomy is not None,
        phase_counts=phase_counts, issue_kinds=issue_kinds, signals=signals,
    )


@router.get("/{farm_id}/intelligence", response_model=FarmIntelligenceOut)
def get_farm_intelligence(
    farm_id: uuid.UUID,
    db: Session = Depends(get_db),
    kb=Depends(knowledge_base),
    prices=Depends(pis),
):
    """
    One summary for the farm: its crops, what the satellite sees (observed), what the
    crop rules say about the operations logged (inferred), the crop's price and 30-day
    outlook and the weather (estimates), each labelled with how it was obtained and
    when. A block with nothing to say, or a service that could not be reached, says so
    inside its own block; the rest of the summary is still returned.
    """
    farm = resolve_farm(db, farm_id)
    if farm is None:
        raise HTTPException(status_code=404, detail="Farm not found")

    farmer = db.get(Farmer, farm.farmer_id)
    cycles = db.execute(select(CropCycle).where(CropCycle.farm_id == farm.id)).scalars().all()
    scenes = db.execute(select(FarmNdviScene).where(FarmNdviScene.farm_id == farm.id)).scalars().all()
    signal_rows = db.execute(
        select(OperationSignal, FarmOperation, CropCycle)
        .join(FarmOperation, FarmOperation.id == OperationSignal.operation_id)
        .join(CropCycle, CropCycle.id == OperationSignal.crop_cycle_id)
        .where(OperationSignal.farm_id == farm.id)
    ).all()
    taxonomy_result = kb.taxonomy()
    newest = max((c for c in cycles if c.crop_id), key=lambda c: c.planted_date, default=None)
    return build_intelligence(
        farm.id, farmer.state if farmer else None, cycles, scenes, [tuple(row) for row in signal_rows], kb, prices,
        taxonomy=taxonomy_result.value if taxonomy_result.ok else None,
        area_ha=farm.area_hectares, transplanted_on=_transplanted_on(db, newest) if newest else None,
        soil_type=farm.soil_type,
    )


def _transplanted_on(db: Session, cycle: CropCycle):
    return db.execute(
        select(func.min(FarmOperation.operation_date)).where(
            FarmOperation.crop_cycle_id == cycle.id, FarmOperation.kb_operation_id == "transplanting"
        )
    ).scalar()


@router.get("/{farm_id}/harvest-estimate", response_model=FarmHarvestEstimateOut)
def get_harvest_estimate(
    farm_id: uuid.UUID,
    db: Session = Depends(get_db),
    kb=Depends(knowledge_base),
    prices=Depends(pis),
):
    """
    An estimate, as a range, of how much the farm's newest crop will give, when, and what it is
    worth. Always labelled an estimate: it is the typical result for the crop on a farm this size,
    from cited current research, not a forecast for this farm. Anything that cannot be worked out
    (no crop, no farm size, no research for the crop, no price) is left out with the reason.
    """
    farm = resolve_farm(db, farm_id)
    if farm is None:
        raise HTTPException(status_code=404, detail="Farm not found")

    cycle = db.execute(
        select(CropCycle).where(CropCycle.farm_id == farm.id, CropCycle.crop_id.is_not(None)).order_by(CropCycle.planted_date.desc()).limit(1)
    ).scalar_one_or_none()
    if cycle is None:
        any_cycle = db.execute(select(CropCycle).where(CropCycle.farm_id == farm.id).order_by(CropCycle.planted_date.desc()).limit(1)).scalar_one_or_none()
        code = "no_crop" if any_cycle else "no_cycle"
        reason = "The crop is not one the Knowledge Base has rules for yet." if any_cycle else "No crop is recorded for this farm."
        return FarmHarvestEstimateOut(farm_id=str(farm.id), status="no_estimate", reason_code=code, reason=reason, crop=any_cycle.crop_raw if any_cycle else None)

    estimate = build_estimate(
        crop_id=cycle.crop_id, crop_name=(cycle.crop_raw or "").strip(), area_ha=farm.area_hectares,
        planted_on=cycle.planted_date, transplanted_on=_transplanted_on(db, cycle), kb=kb, pis=prices,
        soil_type=farm.soil_type,
    )
    return FarmHarvestEstimateOut(farm_id=str(farm.id), **estimate.model_dump())


@router.get("/{farm_id}/ndvi-frames", response_model=NdviFramesOut)
def list_ndvi_frames(farm_id: uuid.UUID, db: Session = Depends(get_db)):
    """
    The pictures of the farm's clear satellite passes, oldest first, to play as a timelapse. Each frame
    is the NDVI picture of one pass with cloud pixels transparent. Frames are kept from the day the
    farm started being recorded, and only for passes where at least half the farm was clear.
    """
    farm = resolve_farm(db, farm_id)
    if farm is None:
        raise HTTPException(status_code=404, detail="Farm not found")
    rows = db.execute(
        select(FarmNdviFrame).where(FarmNdviFrame.farm_id == farm.id).order_by(FarmNdviFrame.scene_date)
    ).scalars().all()
    frames = [
        NdviFrameOut(scene_date=r.scene_date, ndvi_mean=r.ndvi_mean, clear_fraction=r.clear_fraction,
                     image_path=f"/farms/{farm.id}/ndvi-frames/{r.scene_date.isoformat()}")
        for r in rows
    ]
    bbox = corners = None
    boundary = db.execute(select(func.ST_AsGeoJSON(Farm.boundary)).where(Farm.id == farm.id)).scalar_one()
    if boundary:
        ring = json.loads(boundary)["coordinates"][0]
        west, east = min(p[0] for p in ring), max(p[0] for p in ring)
        south, north = min(p[1] for p in ring), max(p[1] for p in ring)
        bbox = [west, south, east, north]
        corners = [[west, north], [east, north], [east, south], [west, south]]
    return NdviFramesOut(farm_id=farm.id, bbox=bbox, corners=corners, count=len(frames), frames=frames)


@router.get("/{farm_id}/ndvi-frames/{scene_date}")
def get_ndvi_frame(farm_id: uuid.UUID, scene_date: date, db: Session = Depends(get_db)):
    """One frame as a PNG. A frame never changes once kept, so it can be cached for a long time."""
    farm = resolve_farm(db, farm_id)
    if farm is None:
        raise HTTPException(status_code=404, detail="Farm not found")
    frame = db.execute(
        select(FarmNdviFrame).where(FarmNdviFrame.farm_id == farm.id, FarmNdviFrame.scene_date == scene_date)
    ).scalar_one_or_none()
    if frame is None:
        raise HTTPException(status_code=404, detail="No frame for this farm on that date")
    try:
        data = read_png(frame.image_pointer)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"The picture could not be read from storage: {exc}")
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "public, max-age=31536000, immutable"})


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
