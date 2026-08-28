from datetime import date, datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter


class WebhookFarm(BaseModel):
    agtrail_farm_id: str
    name: str
    state: str
    lga: str
    hectares: float
    latitude: float
    longitude: float


class WebhookFarmer(BaseModel):
    agtrail_farmer_id: str
    name: str
    phone: str


class WebhookCropCycle(BaseModel):
    agtrail_cycle_id: str
    crop: str
    variety: str | None = None
    planted_date: date
    status: str


class WebhookOperationContext(BaseModel):
    logged_operation_type: str | None = None


class WebhookImage(BaseModel):
    url: str
    captured_at: datetime
    latitude: float | None = None
    longitude: float | None = None


class FarmCreatedPayload(BaseModel):
    event_id: str
    event_type: Literal["farm.created"]
    timestamp: datetime
    farm: WebhookFarm
    farmer: WebhookFarmer


class CropCycleCreatedPayload(BaseModel):
    event_id: str
    event_type: Literal["farm.crop_cycle.created"]
    timestamp: datetime
    farm: WebhookFarm
    farmer: WebhookFarmer
    crop_cycle: WebhookCropCycle


class MonitoringImageCreatedPayload(BaseModel):
    event_id: str
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