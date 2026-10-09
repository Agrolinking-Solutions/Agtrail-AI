"""Tests for the farm location endpoint, used by a frontend map's fly-to view."""

import pytest
from geoalchemy2 import WKTElement

from tests.conftest import API_KEY, requires_db

pytestmark = requires_db


def _make_farm(db_session, **kwargs):
    from app.models.farm import Farm
    from app.models.farmer import Farmer

    farmer = Farmer(full_name="Test Farmer", phone="080000000", state="Oyo", lga="Ibadan North")
    db_session.add(farmer)
    db_session.flush()
    farm = Farm(farmer_id=farmer.id, name="Test Farm", **kwargs)
    db_session.add(farm)
    db_session.flush()
    return farm


def test_location_returns_point_and_boundary_for_a_mapped_farm(client, db_session):
    farm = _make_farm(
        db_session,
        coordinates=WKTElement("POINT(7.95 4.72)", srid=4326),
        boundary_mapped=True,
        boundary=WKTElement("POLYGON((7.9 4.7, 7.99 4.7, 7.99 4.75, 7.9 4.75, 7.9 4.7))", srid=4326),
    )
    db_session.commit()

    response = client.get(f"/farms/{farm.id}/location", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["farm_id"] == str(farm.id)
    assert body["longitude"] == 7.95
    assert body["latitude"] == 4.72
    assert body["boundary_mapped"] is True
    assert body["boundary"]["type"] == "Polygon"


def test_location_has_no_boundary_for_an_unmapped_farm(client, db_session):
    farm = _make_farm(
        db_session,
        coordinates=WKTElement("POINT(7.95 4.72)", srid=4326),
        boundary_mapped=False,
    )
    db_session.commit()

    response = client.get(f"/farms/{farm.id}/location", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["boundary_mapped"] is False
    assert body["boundary"] is None
    assert body["latitude"] == 4.72


def test_location_falls_back_to_boundary_centroid_when_coordinates_missing(client, db_session):
    farm = _make_farm(
        db_session,
        boundary_mapped=True,
        boundary=WKTElement("POLYGON((7.9 4.7, 7.9 4.8, 8.0 4.8, 8.0 4.7, 7.9 4.7))", srid=4326),
    )
    db_session.commit()

    response = client.get(f"/farms/{farm.id}/location", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["longitude"] == pytest.approx(7.95)
    assert body["latitude"] == pytest.approx(4.75)


def test_location_has_no_point_when_neither_coordinates_nor_boundary_exist(client, db_session):
    farm = _make_farm(db_session)
    db_session.commit()

    response = client.get(f"/farms/{farm.id}/location", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["latitude"] is None
    assert body["longitude"] is None
    assert body["boundary"] is None


def test_location_404_for_unknown_farm(client):
    import uuid

    response = client.get(f"/farms/{uuid.uuid4()}/location", headers={"X-API-Key": API_KEY})

    assert response.status_code == 404


def test_location_requires_api_key(client, db_session):
    farm = _make_farm(db_session)
    db_session.commit()

    response = client.get(f"/farms/{farm.id}/location")

    assert response.status_code == 401
