from datetime import date, datetime
from pydantic import BaseModel, Field


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


class AgtrailWebhookPayload(BaseModel):
    event_id: str
    event_type: str
    timestamp: datetime
    farm: WebhookFarm
    farmer: WebhookFarmer
    crop_cycle: WebhookCropCycle
    operation_context: WebhookOperationContext | None = None
    image: WebhookImage