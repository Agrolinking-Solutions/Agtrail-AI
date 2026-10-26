"""Webhook and predictions API tests, against a real PostGIS database."""

import copy

import pytest
from sqlalchemy import func, select

from tests.conftest import API_KEY, load_payload, post_event, queued_image_ids, requires_db

pytestmark = requires_db


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_farm_created(client, db_session):
    from app.models import AgtrailSyncLog, Farm, Farmer

    response = post_event(client, load_payload("test_payload_farm_created.json"))

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "received"
    farm = db_session.execute(select(Farm)).scalar_one()
    assert farm.coordinates is not None
    assert _count(db_session, Farmer) == 1
    log = db_session.execute(select(AgtrailSyncLog)).scalar_one()
    assert log.sync_status.value == "success"
    assert "phone" not in log.payload["farmer"]


def test_crop_cycle_updates_commodity(client, db_session):
    from app.models import Farm

    post_event(client, load_payload("test_payload_farm_created.json"))
    response = post_event(client, load_payload("test_payload_crop_cycle.json"))

    assert response.status_code == 200, response.text
    farm = db_session.execute(select(Farm)).scalar_one()
    assert farm.commodity == "Maize"


def test_monitoring_image_queues_inference(client, db_session):
    from app.models import FarmImage, FarmVisit

    response = post_event(client, load_payload("test_payload.json"))

    assert response.status_code == 200, response.text
    image = db_session.execute(select(FarmImage)).scalar_one()
    assert queued_image_ids(db_session) == [str(image.id)]
    # No bucket configured in tests, so archiving is off.
    assert queued_image_ids(db_session, job_type="archive_image") == []
    assert "phone" not in image.source_payload_raw["farmer"]
    # No mapped boundary yet, so the visit can't be GPS verified.
    assert db_session.execute(select(FarmVisit)).scalar_one().gps_verified is False


def test_gps_verified_inside_mapped_boundary(client, db_session):
    from geoalchemy2 import WKTElement

    from app.models import Farm, FarmVisit

    post_event(client, load_payload("test_payload_farm_created.json"))
    farm = db_session.execute(select(Farm)).scalar_one()
    farm.agtrail_farm_id = "TEST-FARM-001"
    farm.boundary = WKTElement("POLYGON((7.9 4.7, 7.99 4.7, 7.99 4.75, 7.9 4.75, 7.9 4.7))", srid=4326)
    farm.boundary_mapped = True
    db_session.commit()

    post_event(client, load_payload("test_payload.json"))

    db_session.expire_all()
    assert db_session.execute(select(FarmVisit)).scalar_one().gps_verified is True


def test_duplicate_event_ignored(client, db_session):
    from app.models import FarmImage

    payload = load_payload("test_payload.json")
    post_event(client, payload)
    response = post_event(client, payload)

    assert response.json()["status"] == "already_processed"
    assert _count(db_session, FarmImage) == 1
    assert len(queued_image_ids(db_session)) == 1


def test_farmers_sharing_a_phone(client, db_session):
    from app.models import Farmer

    first = load_payload("test_payload_farm_created.json")
    second = copy.deepcopy(first)
    second["event_id"] = "evt_second_farmer"
    second["farmer"]["agtrail_farmer_id"] = "TEST-FARMER-SPOUSE"
    second["farm"]["agtrail_farm_id"] = "TEST-FARM-SPOUSE"

    assert post_event(client, first).status_code == 200
    response = post_event(client, second)

    assert response.status_code == 200, response.text
    assert _count(db_session, Farmer) == 2


def test_missing_signature_rejected(client, db_session):
    from app.models import Farm

    response = post_event(client, load_payload("test_payload_farm_created.json"), signature=None)

    assert response.status_code == 401
    assert _count(db_session, Farm) == 0


def test_wrong_signature_rejected(client):
    response = post_event(client, load_payload("test_payload_farm_created.json"), signature="0" * 64)
    assert response.status_code == 401


def test_oversized_body_rejected(client):
    payload = load_payload("test_payload_farm_created.json")
    payload["padding"] = "x" * 300_000
    assert post_event(client, payload).status_code == 413


def test_overlong_field_is_422_not_500(client):
    payload = load_payload("test_payload_farm_created.json")
    payload["farmer"]["phone"] = "0" * 50
    assert post_event(client, payload).status_code == 422


def _make_prediction(db_session):
    from app.models import FarmImage, FarmStage, Prediction

    image = db_session.execute(select(FarmImage)).scalar_one()
    prediction = Prediction(
        image_id=image.id,
        farm_id=image.visit.farm_id,
        model_version="sha256:test",
        predicted_stage=FarmStage.planting,
        confidence=0.9,
    )
    db_session.add(prediction)
    db_session.commit()
    return prediction


def test_predictions_require_api_key(client, db_session):
    post_event(client, load_payload("test_payload.json"))
    prediction = _make_prediction(db_session)

    assert client.get(f"/predictions/{prediction.farm_id}").status_code == 401
    assert client.post(f"/predictions/{prediction.id}/verify", json={"verified": True}).status_code == 401

    response = client.get(f"/predictions/{prediction.farm_id}", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    assert response.json()["predicted_stage"] == "planting"


def test_verify_keeps_original_prediction(client, db_session):
    post_event(client, load_payload("test_payload.json"))
    prediction = _make_prediction(db_session)

    response = client.post(
        f"/predictions/{prediction.id}/verify",
        json={"verified": False, "corrected_stage": "weeding"},
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["predicted_stage"] == "planting"
    assert body["corrected_stage"] == "weeding"
    assert body["verified_by"] == "tester"
    assert body["verified_at"] is not None


def test_readiness_checks_database(client):
    assert client.get("/health/ready").json() == {"status": "ok"}


@pytest.mark.parametrize("setting", ["S3_BUCKET", "GCP_BUCKET_NAME"])
def test_monitoring_image_queues_archive_when_bucket_configured(client, db_session, monkeypatch, setting):
    from sqlalchemy import select

    from app.core.config import settings
    from app.models.image import FarmImage

    monkeypatch.setattr(settings, setting, "test-bucket")

    response = post_event(client, load_payload("test_payload.json"))

    assert response.status_code == 200, response.text
    image = db_session.execute(select(FarmImage)).scalar_one()
    assert queued_image_ids(db_session, job_type="archive_image") == [str(image.id)]


def _with_soil(payload, **fields):
    payload = copy.deepcopy(payload)
    payload["farm"].update(fields)
    return payload


@pytest.mark.parametrize("key", ["soil_type", "soilType", "soiltype"])
def test_farm_soil_type_is_stored(client, db_session, key):
    from app.models import Farm

    response = post_event(client, _with_soil(load_payload("test_payload_farm_created.json"), **{key: " Sandy loam "}))

    assert response.status_code == 200, response.text
    assert db_session.execute(select(Farm)).scalar_one().soil_type == "Sandy loam"


def test_farm_without_soil_type_is_still_accepted(client, db_session):
    from app.models import Farm

    response = post_event(client, load_payload("test_payload_farm_created.json"))

    assert response.status_code == 200, response.text
    assert db_session.execute(select(Farm)).scalar_one().soil_type is None


@pytest.mark.parametrize("odd", [{"name": "loamy"}, ["loamy"], True, "", None])
def test_unusable_soil_type_never_rejects_the_event(client, db_session, odd):
    from app.models import Farm

    response = post_event(client, _with_soil(load_payload("test_payload_farm_created.json"), soil_type=odd))

    assert response.status_code == 200, response.text
    assert db_session.execute(select(Farm)).scalar_one().soil_type is None


def test_soil_type_added_later_and_not_cleared_by_a_message_without_it(client, db_session):
    from app.models import Farm

    base = load_payload("test_payload_farm_created.json")
    post_event(client, base)
    later = _with_soil(base, soil_type="Clay")
    later["event_id"] = "evt-soil-later"
    post_event(client, later)
    again = copy.deepcopy(base)
    again["event_id"] = "evt-no-soil"
    post_event(client, again)

    db_session.expire_all()
    assert db_session.execute(select(Farm)).scalar_one().soil_type == "Clay"


def test_unread_farm_fields_are_kept_in_the_stored_event(client, db_session):
    from app.models import AgtrailSyncLog

    post_event(client, _with_soil(load_payload("test_payload_farm_created.json"), soil_texture="loamy"))

    assert db_session.execute(select(AgtrailSyncLog)).scalar_one().payload["farm"]["soil_texture"] == "loamy"


# ---- AgTrail's placeholders (26 Oct 2026): "Not recorded", 0 for missing numbers, a farmer id that can be missing ----

def _payload(**farm):
    p = copy.deepcopy(load_payload("test_payload_farm_created.json"))
    p["farm"].update(farm)
    return p


def test_not_recorded_soil_is_no_soil_and_never_overwrites_a_real_one(client, db_session):
    from app.models import Farm

    p = _payload(soil_type="sandy_loam")
    post_event(client, p)
    again = _payload(soil_type="Not recorded")
    again["event_id"] = "evt-not-recorded"
    post_event(client, again)

    db_session.expire_all()
    assert db_session.execute(select(Farm)).scalar_one().soil_type == "sandy_loam"


def test_a_new_farm_with_not_recorded_soil_has_none(client, db_session):
    from app.models import Farm

    post_event(client, _payload(soil_type="Not recorded"))
    assert db_session.execute(select(Farm)).scalar_one().soil_type is None


def test_zero_area_and_zero_location_mean_missing(client, db_session):
    from app.models import Farm

    post_event(client, _payload(hectares=0, latitude=0, longitude=0))
    farm = db_session.execute(select(Farm)).scalar_one()
    assert farm.area_hectares is None and farm.coordinates is None


def test_area_and_location_arrive_later_without_replacing_real_ones(client, db_session):
    from app.models import Farm

    post_event(client, _payload(hectares=0, latitude=0, longitude=0))
    later = _payload(hectares=1.5, latitude=8.1, longitude=4.2)
    later["event_id"] = "evt-later"
    post_event(client, later)
    other = _payload(hectares=9, latitude=1, longitude=1)
    other["event_id"] = "evt-other"
    post_event(client, other)

    db_session.expire_all()
    farm = db_session.execute(select(Farm)).scalar_one()
    assert farm.area_hectares == 1.5 and farm.coordinates is not None


def test_a_missing_farmer_id_does_not_reject_the_event(client, db_session):
    from app.models import Farm, Farmer

    p = copy.deepcopy(load_payload("test_payload_farm_created.json"))
    p["farmer"]["agtrail_farmer_id"] = None
    assert post_event(client, p).status_code == 200
    again = copy.deepcopy(p)
    again["event_id"] = "evt-again"
    assert post_event(client, again).status_code == 200

    assert _count(db_session, Farmer) == 1 and _count(db_session, Farm) == 1


def test_a_known_place_replaces_not_recorded_but_not_a_real_one(client, db_session):
    from app.models import Farmer

    post_event(client, _payload(state="Not recorded", lga="Not recorded"))
    later = _payload(state="Ondo State", lga="Idanre")
    later["event_id"] = "evt-place"
    post_event(client, later)
    other = _payload(state="Oyo", lga="Ibadan North")
    other["event_id"] = "evt-place2"
    post_event(client, other)

    db_session.expire_all()
    farmer = db_session.execute(select(Farmer)).scalar_one()
    assert (farmer.state, farmer.lga) == ("Ondo State", "Idanre")
