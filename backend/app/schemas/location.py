import uuid

from pydantic import BaseModel


class FarmLocationOut(BaseModel):
    farm_id: uuid.UUID
    latitude: float | None
    longitude: float | None
    boundary_mapped: bool
    boundary: dict | None
