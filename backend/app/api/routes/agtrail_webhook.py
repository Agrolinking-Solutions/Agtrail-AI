import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, BackgroundTasks
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.farmer import Farmer
from app.models.farm import Farm
from app.models.visit import FarmVisit
from app.models.image import FarmImage, ImageSource, ImageStatus
from app.schemas.agtrail_webhook import AgtrailWebhookPayload
from app.services.agtrail_webhook_security import verify_webhook_signature
from app.services.agtrail_webhook_idempotency import event_already_received, record_event_received
from app.services.inference import run_inference_for_image

router = APIRouter(prefix="/webhooks/agtrail", tags=["agtrail-webhook"])


def _get_or_create_farmer(db: Session, payload: AgtrailWebhookPayload) -> Farmer:
    # Temporary, minimal lookup logic. This will move into the dedicated
    # schema mapper module once it exists, this is just enough to keep
    # the webhook receiver functional on its own for now.
    farmer = db.execute(
        select(Farmer).where(Farmer.agtrail_farmer_id == payload.farmer.agtrail_farmer_id)
    ).scalar_one_or_none()

    if farmer is None:
        farmer = Farmer(
            full_name=payload.farmer.name,
            phone=payload.farmer.phone,
            state=payload.farm.state,
            lga=payload.farm.lga,
            agtrail_farmer_id=payload.farmer.agtrail_farmer_id,
        )
        db.add(farmer)
        db.flush()

    return farmer


def _get_or_create_farm(db: Session, payload: AgtrailWebhookPayload, farmer: Farmer) -> Farm:
    farm = db.execute(
        select(Farm).where(Farm.agtrail_farm_id == payload.farm.agtrail_farm_id)
    ).scalar_one_or_none()

    if farm is None:
        farm = Farm(
            farmer_id=farmer.id,
            name=payload.farm.name,
            area_hectares=payload.farm.hectares,
            commodity=payload.crop_cycle.crop,
            agtrail_farm_id=payload.farm.agtrail_farm_id,
        )
        db.add(farm)
        db.flush()

    return farm


@router.post("/farm-event")
async def receive_agtrail_farm_event(
    request: Request,
    background_tasks: BackgroundTasks,
    x_agtrail_signature: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    raw_body = await request.body()

    if not verify_webhook_signature(raw_body, x_agtrail_signature):
        raise HTTPException(status_code=401, detail="Invalid or missing signature")

    try:
        payload = AgtrailWebhookPayload.model_validate_json(raw_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors())

    if event_already_received(db, payload.event_id):
        return {"status": "already_processed", "event_id": payload.event_id}

    farmer = _get_or_create_farmer(db, payload)
    farm = _get_or_create_farm(db, payload, farmer)

    visit = FarmVisit(
        farm_id=farm.id,
        agent_id=None,
        gps_lat=payload.image.latitude,
        gps_lng=payload.image.longitude,
        gps_verified=False,
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
        source_payload_raw=payload.model_dump(mode="json"),
    )
    db.add(image)
    db.flush()

    record_event_received(db, payload.event_id, farm.id, payload.model_dump(mode="json"))
    db.commit()

    background_tasks.add_task(run_inference_for_image, image.id)

    return {"status": "received", "event_id": payload.event_id, "farm_id": str(farm.id)}