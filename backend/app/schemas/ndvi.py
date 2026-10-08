from datetime import date

from pydantic import BaseModel, ConfigDict


class NdviObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period_start: date
    period_end: date
    ndvi_mean: float
    valid_pixel_count: int
