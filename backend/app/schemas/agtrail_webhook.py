from datetime import date, datetime
from typing import Annotated, Literal, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, TypeAdapter, field_validator

# Length limits match the database columns, so an over-long value is a
# 422 here rather than a database error (and endless retries) later.
AgtrailId = Annotated[str, Field(min_length=1, max_length=200)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


# AgTrail fills a missing text field with one of these instead of leaving it empty.
PLACEHOLDERS = {"not recorded", "unknown", "unnamed farm"}


class WebhookFarm(BaseModel):
    # Fields we do not read yet are kept (and so end up in the stored copy of the event), so we can
    # see what AgTrail really sends instead of guessing.
    model_config = ConfigDict(extra="allow")

    agtrail_farm_id: AgtrailId
    name: str = Field(max_length=200)
    state: str = Field(max_length=100)
    lga: str = Field(max_length=100)
    hectares: float = Field(ge=0)
    latitude: Latitude
    longitude: Longitude
    # The soil the farm is on, as the farmer or agent picked it ("loamy", "sandy"...). Optional, and
    # never a reason to reject an event: anything unusable becomes None.
    soil_type: str | None = Field(
        default=None, validation_alias=AliasChoices("soil_type", "soilType", "soiltype", "soil")
    )

    @field_validator("soil_type", mode="before")
    @classmethod
    def _soil_type_text(cls, value):
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            text = str(value).strip()[:100]
            # AgTrail writes "Not recorded" when it has nothing: that is no soil, not a soil.
            return None if not text or text.lower() in PLACEHOLDERS else text
        return None

    @property
    def known_hectares(self) -> float | None:
        """AgTrail sends 0 when the area is not on record."""
        return self.hectares if self.hectares and self.hectares > 0 else None

    @property
    def has_location(self) -> bool:
        """AgTrail sends 0, 0 when there is no location on file."""
        return not (self.latitude == 0 and self.longitude == 0)


class WebhookFarmer(BaseModel):
    # AgTrail says this is rarely missing. A missing one must not make the whole event a 422.
    agtrail_farmer_id: AgtrailId | None = None
    name: str = Field(max_length=200)
    phone: str = Field(max_length=20)


class WebhookCropCycle(BaseModel):
    agtrail_cycle_id: AgtrailId
    crop: str = Field(max_length=100)
    variety: str | None = Field(default=None, max_length=200)
    planted_date: date
    status: str = Field(max_length=50)


class WebhookOperationContext(BaseModel):
    agtrail_operation_id: str | None = Field(default=None, max_length=200)
    logged_operation_type: str | None = Field(default=None, max_length=100)
    # The date the agent says the operation happened. Optional, because older
    # AgTrail versions do not send it, and forgiving on purpose: an unreadable
    # value becomes None (we then use the photo's date) instead of rejecting
    # the whole event, which AgTrail would retry forever.
    operation_date: date | None = None

    @field_validator("operation_date", mode="before")
    @classmethod
    def _read_operation_date(cls, value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value.strip()[:10])
            except ValueError:
                return None
        return None


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
