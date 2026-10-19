import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_api_key
from app.schemas.check import BoundaryIn, CentroidOut, CheckOut, NdviSummaryOut
from app.services.mapbox import fetch_area_view, fetch_boundary_satellite_image
from app.services.sentinel import IMAGE_STYLES, default_window, fetch_farm_image, fetch_farm_ndvi, get_access_token

router = APIRouter(
    prefix="/check",
    tags=["check"],
    dependencies=[Depends(require_api_key)],
)

# A plot this small is almost certainly a malformed submission (a point, or
# a sliver with near-zero area), not a real farm; this large is implausible
# for a single-plot check and more likely a units mistake (whole-country
# coordinates, or degrees entered as something else).
MIN_AREA_HECTARES = 0.001
MAX_AREA_HECTARES = 5000

_GEOMETRY_QUERY = text(
    """
    SELECT
        ST_IsValid(g) AS is_valid,
        ST_Area(g::geography) / 10000 AS area_hectares,
        ST_Y(ST_Centroid(g)) AS latitude,
        ST_X(ST_Centroid(g)) AS longitude,
        ST_AsGeoJSON(g) AS geojson
    FROM (SELECT ST_SetSRID(ST_GeomFromGeoJSON(:geojson), 4326) AS g) AS sub
    """
)


def _resolve_geometry(db: Session, boundary: dict) -> tuple[float, float, float, dict]:
    """
    Validates a caller-submitted GeoJSON polygon and returns its area
    (hectares), centroid (latitude, longitude) and a normalized GeoJSON
    dict, using PostGIS rather than a second geometry library, since
    every other geometry operation in this codebase already goes
    through Postgres. Raises a 422 for anything that isn't a sane,
    single plot.
    """
    try:
        geojson_text = json.dumps(boundary)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="boundary is not JSON-serializable")

    try:
        row = db.execute(_GEOMETRY_QUERY, {"geojson": geojson_text}).first()
    except DBAPIError:
        db.rollback()
        raise HTTPException(status_code=422, detail="boundary is not a valid GeoJSON geometry")

    if row is None or not row.is_valid or row.area_hectares is None:
        raise HTTPException(status_code=422, detail="boundary is not a valid GeoJSON geometry")
    if row.area_hectares < MIN_AREA_HECTARES:
        raise HTTPException(status_code=422, detail="boundary's area is too small to be a real plot")
    if row.area_hectares > MAX_AREA_HECTARES:
        raise HTTPException(status_code=422, detail="boundary's area is implausibly large for a single plot")

    return row.area_hectares, row.latitude, row.longitude, json.loads(row.geojson)


@router.post("", response_model=CheckOut)
def check_plot(body: BoundaryIn, db: Session = Depends(get_db)):
    """
    A one-shot health check for a plot that isn't necessarily an AgTrail
    farm: the caller supplies the boundary directly. Returns its size,
    center point, and the current NDVI reading, when a clear Sentinel-2
    composite exists for the recent window; "ndvi" is null rather than
    an error when the plot is real but no cloud-free pass has happened
    recently, which is normal and not a sign anything is wrong.
    """
    area_hectares, lat, lng, boundary_geojson = _resolve_geometry(db, body.boundary)

    start, end = default_window()
    token = get_access_token()
    periods = fetch_farm_ndvi(boundary_geojson, start, end, token)
    latest = periods[-1] if periods else None

    return CheckOut(
        area_hectares=round(area_hectares, 4),
        centroid=CentroidOut(latitude=lat, longitude=lng),
        ndvi=NdviSummaryOut(**vars(latest)) if latest else None,
    )


@router.post("/image")
def check_plot_image(
    body: BoundaryIn,
    style: str = Query(default="ndvi", pattern="^(ndvi|true-color|mapbox|mapbox-area)$"),
    db: Session = Depends(get_db),
):
    """
    Same four styles as /farms/{farm_id}/ndvi-image, for a plot that
    isn't necessarily an AgTrail farm. Not cached: each call is a
    billable action against a caller-supplied geometry, not a repeat
    view of a farm already on file.
    """
    area_hectares, lat, lng, boundary_geojson = _resolve_geometry(db, body.boundary)

    if style == "mapbox":
        return Response(content=fetch_boundary_satellite_image(boundary_geojson), media_type="image/png")
    if style == "mapbox-area":
        return Response(content=fetch_area_view(lng, lat, boundary_geojson), media_type="image/png")

    start, end = default_window()
    token = get_access_token()
    png_bytes = fetch_farm_image(IMAGE_STYLES[style], boundary_geojson, start, end, token)
    return Response(content=png_bytes, media_type="image/png")
