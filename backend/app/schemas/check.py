from datetime import date

from pydantic import BaseModel, Field


class BoundaryIn(BaseModel):
    """A GeoJSON Polygon for a plot that isn't necessarily an AgTrail farm."""

    boundary: dict = Field(description="A GeoJSON Polygon describing the plot to check.")


class CentroidOut(BaseModel):
    latitude: float
    longitude: float


class NdviSummaryOut(BaseModel):
    period_start: date
    period_end: date
    ndvi_mean: float
    valid_pixel_count: int


class CheckOut(BaseModel):
    area_hectares: float
    centroid: CentroidOut
    ndvi: NdviSummaryOut | None
