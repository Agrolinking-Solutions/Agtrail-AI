from datetime import date, datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

# Length limits match the database columns, so an over-long value is a
# 422 here rather than a database error (and endless retries) later.
AgtrailId = Annotated[str, Field(min_length=1, max_length=200)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class WebhookFarm(BaseModel):
    agtrail_farm_id: AgtrailId
    name: str = Field(max_length=200)
    state: str = Field(max_length=100)
    lga: str = Field(max_length=100)
    hectares: float = Field(ge=0)
    latitude: Latitude
    longitude: Longitude


class WebhookFarmer(BaseModel):
    agtrail_farmer_id: AgtrailId
    name: str = Field(max_length=200)
    phone: str = Field(max_length=20)


class WebhookCropCycle(BaseModel):
    agtrail_cycle_id: AgtrailId
    crop: str = Field(max_length=100)
    variety: str | None = Field(default=None, max_length=200)
    planted_date: date
    status: str = Field(max_length=50)


class WebhookOperationContext(BaseModel):
    logged_operation_type: str | None = Field(default=None, max_length=100)


class WebhookImage(BaseModel):
    url: str = Field(max_length=500)
    captured_at: datetime
    latitude: Latitude | None = None
    longitude: Longitude | None = None


class FarmCreatedPayload(BaseModel):
    event_id: AgtrailId
    event_type: Literal["farm.created"]
    timestamp: datetime
    farm: WebhookFarm
    farmer: WebhookFarmer


class CropCycleCreatedPayload(BaseModel):
    event_id: AgtrailId
    event_type: Literal["farm.crop_cycle.created"]
    timestamp: datetime
    farm: WebhookFarm
    farmer: WebhookFarmer
    crop_cycle: WebhookCropCycle


class MonitoringImageCreatedPayload(BaseModel):
    event_id: AgtrailId
    event_type: Literal["farm.monitoring_image.created"]
    timestamp: datetime
    farm: WebhookFarm
    farmer: WebhookFarmer
    crop_cycle: WebhookCropCycle
    operation_context: WebhookOperationContext | None = None
    image: WebhookImage


AgtrailWebhookEvent = Annotated[
    Union[FarmCreatedPayload, CropCycleCreatedPayload, MonitoringImageCreatedPayload],
    Field(discriminator="event_type"),
]

agtrail_webhook_adapter: TypeAdapter = TypeAdapter(AgtrailWebhookEvent)

# Farmer personal details are stored once, on the farmers table, and left
# out of the raw payload copies kept for debugging.
PII_EXCLUDE = {"farmer": {"name", "phone"}}


def redacted_payload(payload) -> dict:
    return payload.model_dump(mode="json", exclude=PII_EXCLUDE)
