"""Webhook and predictions API tests, against a real PostGIS database."""

import copy

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


def test_monitoring_image_queues_archive_when_bucket_configured(client, db_session, monkeypatch):
    from sqlalchemy import select

    from app.core.config import settings
    from app.models.image import FarmImage

    monkeypatch.setattr(settings, "GCP_BUCKET_NAME", "test-bucket")

    response = post_event(client, load_payload("test_payload.json"))

    assert response.status_code == 200, response.text
    image = db_session.execute(select(FarmImage)).scalar_one()
    assert queued_image_ids(db_session, job_type="archive_image") == [str(image.id)]
