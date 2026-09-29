import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from geoalchemy2 import WKTElement
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.farmer import Farmer
from app.models.farm import Farm
from app.models.visit import FarmVisit
from app.models.image import FarmImage, ImageSource, ImageStatus
from app.schemas.agtrail_webhook import (
    agtrail_webhook_adapter,
    redacted_payload,
    FarmCreatedPayload,
    CropCycleCreatedPayload,
    MonitoringImageCreatedPayload,
)
from app.services.agtrail_webhook_security import verify_webhook_signature
from app.services.agtrail_webhook_idempotency import event_already_received, record_event_received
from app.services.job_queue import JOB_RUN_INFERENCE, enqueue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/agtrail", tags=["agtrail-webhook"])


def _point(lat: float, lng: float) -> WKTElement:
    return WKTElement(f"POINT({lng} {lat})", srid=4326)


async def _read_limited_body(request: Request) -> bytes:
    """
    Reads the request body, refusing anything over WEBHOOK_MAX_BODY_BYTES.
    This runs before the signature check, so without a cap an
    unauthenticated sender could make the server buffer any amount.
    """
    limit = settings.WEBHOOK_MAX_BODY_BYTES
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="Payload too large")

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status_code=413, detail="Payload too large")
    return bytes(body)


def _get_or_create_farmer(db: Session, farm_data, farmer_data) -> Farmer:
    # Temporary, minimal lookup logic. This will move into the dedicated
    # schema mapper module once it exists.
    farmer = db.execute(
        select(Farmer).where(Farmer.agtrail_farmer_id == farmer_data.agtrail_farmer_id)
    ).scalar_one_or_none()

    if farmer is None:
        farmer = Farmer(
            full_name=farmer_data.name,
            phone=farmer_data.phone,
            state=farm_data.state,
            lga=farm_data.lga,
            agtrail_farmer_id=farmer_data.agtrail_farmer_id,
        )
        db.add(farmer)
        db.flush()

    return farmer


def _get_or_create_farm(db: Session, farm_data, farmer: Farmer, commodity: str | None = None) -> Farm:
    farm = db.execute(
        select(Farm).where(Farm.agtrail_farm_id == farm_data.agtrail_farm_id)
    ).scalar_one_or_none()

    if farm is None:
        farm = Farm(
            farmer_id=farmer.id,
            name=farm_data.name,
            area_hectares=farm_data.hectares,
            coordinates=_point(farm_data.latitude, farm_data.longitude),
            commodity=commodity,
            agtrail_farm_id=farm_data.agtrail_farm_id,
        )
        db.add(farm)
        db.flush()
        return farm

    # A later crop cycle can change what the farm is growing.
    if commodity is not None:
        farm.commodity = commodity
    if farm.coordinates is None:
        farm.coordinates = _point(farm_data.latitude, farm_data.longitude)

    return farm


def _inside_farm_boundary(db: Session, farm: Farm, lat: float | None, lng: float | None) -> bool:
    """
    True when the photo's GPS position falls within the farm's mapped
    boundary. False when either is missing, so unmapped farms are never
    reported as verified.
    """
    if lat is None or lng is None or not farm.boundary_mapped:
        return False

    return bool(db.scalar(
        select(func.ST_Covers(Farm.boundary, func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326)))
        .where(Farm.id == farm.id)
    ))


def _process_event(db: Session, payload) -> tuple[Farm, FarmImage | None]:
    if isinstance(payload, FarmCreatedPayload):
        farmer = _get_or_create_farmer(db, payload.farm, payload.farmer)
        return _get_or_create_farm(db, payload.farm, farmer), None

    if isinstance(payload, CropCycleCreatedPayload):
        farmer = _get_or_create_farmer(db, payload.farm, payload.farmer)
        # Crop cycle details (variety, planted_date, status) are logged in
        # the raw payload. Mapping these into a dedicated table is part of
        # the future schema mapper work, not handled yet.
        return _get_or_create_farm(db, payload.farm, farmer, commodity=payload.crop_cycle.crop), None

    if isinstance(payload, MonitoringImageCreatedPayload):
        farmer = _get_or_create_farmer(db, payload.farm, payload.farmer)
        farm = _get_or_create_farm(db, payload.farm, farmer, commodity=payload.crop_cycle.crop)

        visit = FarmVisit(
            farm_id=farm.id,
            agent_id=None,
            gps_lat=payload.image.latitude,
            gps_lng=payload.image.longitude,
            gps_verified=_inside_farm_boundary(db, farm, payload.image.latitude, payload.image.longitude),
        )
        db.add(visit)
        db.flush()

        image = FarmImage(
            visit_id=visit.id,
            storage_url=payload.image.url,
            img_lat=payload.image.latitude,
            img_lng=payload.image.longitude,
            captured_at=payload.image.captured_at,
            source=ImageSource.agtrail_webhook,
            status=ImageStatus.pending,
            source_payload_raw=redacted_payload(payload),
        )
        db.add(image)
        db.flush()

        # Enqueued in the same transaction as the image row, not run as a
        # FastAPI BackgroundTask: if the process crashes right after this
        # request commits, the job survives in the database and a worker
        # (app/worker.py) picks it up, instead of the image being silently
        # stuck pending forever.
        enqueue(db, JOB_RUN_INFERENCE, {"image_id": str(image.id)})

        return farm, image

    raise HTTPException(status_code=422, detail="Unsupported event type")


@router.post("/farm-event")
async def receive_agtrail_farm_event(
    request: Request,
    x_agtrail_signature: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    raw_body = await _read_limited_body(request)

    if not verify_webhook_signature(raw_body, x_agtrail_signature):
        raise HTTPException(status_code=401, detail="Invalid or missing signature")

    try:
        payload = agtrail_webhook_adapter.validate_json(raw_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False, include_input=False))

    if event_already_received(db, payload.event_id):
        return {"status": "already_processed", "event_id": payload.event_id}

    try:
        farm, image = _process_event(db, payload)
        record_event_received(db, payload.event_id, farm.id, redacted_payload(payload))
        db.commit()
    except IntegrityError:
        # Another delivery of this event, or of an event for the same new
        # farm or farmer, was saved at the same moment.
        db.rollback()
        if event_already_received(db, payload.event_id):
            return {"status": "already_processed", "event_id": payload.event_id}
        logger.warning("Conflict saving event %s, asking AgTrail to retry.", payload.event_id)
        raise HTTPException(status_code=409, detail="Conflicting concurrent update, retry later")

    return {"status": "received", "event_id": payload.event_id, "farm_id": str(farm.id)}
