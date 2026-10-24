"""
Moving a farm to the trash and getting it back: everything attached comes with it,
restoring puts every row back as it was, and nothing happens without --yes.
"""

import json
import uuid
from datetime import date, datetime, timezone

import pytest
from geoalchemy2 import WKTElement
from sqlalchemy import func, select, text

from app.jobs import trash as trash_cli
from app.models.crop_cycle import CropCycle
from app.models.farm import Farm
from app.models.farm_operation import FarmOperation
from app.models.farmer import Farmer
from app.models.image import FarmImage, ImageStatus
from app.models.ndvi import FarmNdviScene
from app.models.operation_signal import OperationSignal
from app.models.prediction import FarmStage, Prediction
from app.models.trash import TrashedRecord
from app.models.visit import FarmVisit
from app.services import trash
from tests.conftest import requires_db

pytestmark = requires_db

SQUARE = "POLYGON((7.9 4.7, 7.9005 4.7, 7.9005 4.7005, 7.9 4.7005, 7.9 4.7))"


def build_farm(db, agtrail_id="TEST-FARM-001", phone="0800000001", farmer=None):
    farmer = farmer or Farmer(full_name="Test Farmer", phone=phone, state="Kano", lga="Kano Municipal")
    db.add(farmer)
    db.flush()
    farm = Farm(farmer_id=farmer.id, name=f"Farm {agtrail_id}", agtrail_farm_id=agtrail_id, boundary_mapped=True,
                boundary=WKTElement(SQUARE, srid=4326), area_hectares=0.5)
    db.add(farm)
    db.flush()
    cycle = CropCycle(farm_id=farm.id, agtrail_cycle_id=f"CYC-{agtrail_id}", crop_raw="Maize", crop_id="maize",
                      planted_date=date(2026, 8, 1), status="active")
    db.add(cycle)
    db.flush()
    op = FarmOperation(farm_id=farm.id, crop_cycle_id=cycle.id, logged_type_raw="planting", signal_category="planting",
                       kb_operation_id="planting", operation_date=date(2026, 8, 1))
    db.add(op)
    db.flush()
    db.add(OperationSignal(operation_id=op.id, farm_id=farm.id, crop_cycle_id=cycle.id, status="ok", issues=[], kb_version="0.1.0+x"))
    db.add(FarmNdviScene(farm_id=farm.id, scene_date=date(2026, 10, 20), ndvi_mean=0.5, polygon_pixels=11, clear_fraction=1.0))
    visit = FarmVisit(farm_id=farm.id)
    db.add(visit)
    db.flush()
    image = FarmImage(visit_id=visit.id, storage_url="https://x.example/a.jpg", status=ImageStatus.processed,
                      farm_operation_id=op.id, captured_at=datetime(2026, 8, 1, 9, 30, tzinfo=timezone.utc))
    db.add(image)
    db.flush()
    db.add(Prediction(image_id=image.id, farm_id=farm.id, model_version="m1", predicted_stage=list(FarmStage)[0], confidence=0.9))
    db.commit()
    return farm


def count(db, model, **where):
    return db.execute(select(func.count()).select_from(model).filter_by(**where)).scalar()


def test_a_preview_shows_what_would_move_and_changes_nothing(db_session):
    build_farm(db_session)

    info = trash.describe(db_session, "TEST-FARM-001")

    assert info["found"] and info["rows"]["farms"] == 1
    assert {"crop_cycles", "farm_operations", "farm_ndvi_scenes", "operation_signals", "farm_visits", "farm_images", "predictions"} <= set(info["rows"])
    assert count(db_session, Farm, agtrail_farm_id="TEST-FARM-001") == 1 and count(db_session, TrashedRecord) == 0


def test_an_unknown_farm_is_reported_not_guessed(db_session):
    assert trash.describe(db_session, "NOPE")["found"] is False
    with pytest.raises(LookupError):
        trash.trash_farm(db_session, "NOPE", "x")


def test_trashing_removes_the_farm_and_everything_attached_and_keeps_a_whole_copy(db_session):
    build_farm(db_session)

    record = trash.trash_farm(db_session, "TEST-FARM-001", "test record")
    db_session.commit()

    for model in (Farm, CropCycle, FarmOperation, OperationSignal, FarmNdviScene, FarmVisit, FarmImage, Prediction, Farmer):
        assert count(db_session, model) == 0, model.__name__
    stored = db_session.get(TrashedRecord, record.id)
    assert stored.reason == "test record" and stored.restored_at is None and "TEST-FARM-001" in stored.label
    assert {name: len(rows) for name, rows in stored.payload["tables"].items()}["farm_images"] == 1
    assert stored.payload["farmer_removed"] is True
    assert "POLYGON" in stored.payload["tables"]["farms"][0]["boundary"]


def test_other_farms_are_left_alone_and_a_shared_farmer_stays(db_session):
    first = build_farm(db_session, "TEST-FARM-001")
    farmer = db_session.get(Farmer, first.farmer_id)
    build_farm(db_session, "REAL-FARM-1", farmer=farmer)

    record = trash.trash_farm(db_session, "TEST-FARM-001", "test record")
    db_session.commit()

    assert count(db_session, Farm) == 1 and count(db_session, Farm, agtrail_farm_id="REAL-FARM-1") == 1
    assert count(db_session, Farmer) == 1 and record.payload["farmer_removed"] is False
    assert count(db_session, CropCycle) == 1 and count(db_session, FarmImage) == 1


def test_restoring_puts_every_row_back_exactly_as_it_was(db_session):
    farm = build_farm(db_session)
    before = {
        "farm": (farm.id, farm.name, farm.area_hectares),
        "shape": db_session.execute(text("select ST_AsText(boundary) from farms")).scalar(),
        "image": db_session.execute(select(FarmImage.status, FarmImage.captured_at, FarmImage.farm_operation_id)).one(),
        "scene": db_session.execute(select(FarmNdviScene.scene_date, FarmNdviScene.ndvi_mean)).one(),
        "prediction": db_session.execute(select(Prediction.predicted_stage, Prediction.confidence)).one(),
    }
    record = trash.trash_farm(db_session, "TEST-FARM-001", "test record")
    db_session.commit()

    restored = trash.restore_record(db_session, record.id)
    db_session.commit()

    assert restored["farms"] == 1 and restored["farmers"] == 1 and restored["predictions"] == 1
    after_farm = db_session.execute(select(Farm)).scalar_one()
    assert (after_farm.id, after_farm.name, after_farm.area_hectares) == before["farm"]
    assert db_session.execute(text("select ST_AsText(boundary) from farms")).scalar() == before["shape"]
    assert tuple(db_session.execute(select(FarmImage.status, FarmImage.captured_at, FarmImage.farm_operation_id)).one()) == tuple(before["image"])
    assert tuple(db_session.execute(select(FarmNdviScene.scene_date, FarmNdviScene.ndvi_mean)).one()) == tuple(before["scene"])
    assert tuple(db_session.execute(select(Prediction.predicted_stage, Prediction.confidence)).one()) == tuple(before["prediction"])
    assert db_session.get(TrashedRecord, record.id).restored_at is not None


def test_a_record_cannot_be_restored_twice(db_session):
    build_farm(db_session)
    record = trash.trash_farm(db_session, "TEST-FARM-001", "x")
    db_session.commit()
    trash.restore_record(db_session, record.id)
    db_session.commit()

    with pytest.raises(ValueError, match="already restored"):
        trash.restore_record(db_session, record.id)


def test_the_export_is_plain_json_with_the_reason_and_every_table(db_session):
    build_farm(db_session)
    record = trash.trash_farm(db_session, "TEST-FARM-001", "test record in production")
    db_session.commit()

    exported = trash.export_record(db_session, record.id)

    again = json.loads(json.dumps(exported))
    assert again["reason"] == "test record in production" and again["entity_id"] == "TEST-FARM-001"
    assert {"farms", "crop_cycles", "farm_operations", "farm_images"} <= set(again["payload"]["tables"])


def test_the_command_does_nothing_without_yes_and_works_with_it(db_session, monkeypatch, capsys):
    build_farm(db_session)
    monkeypatch.setattr(trash_cli, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    assert trash_cli.main(["farm", "TEST-FARM-001"]) == 0
    assert "Nothing was moved" in capsys.readouterr().out and count(db_session, Farm) == 1

    assert trash_cli.main(["farm", "TEST-FARM-001", "--yes", "--reason", "test record"]) == 0
    out = capsys.readouterr().out
    assert "Trash id:" in out and count(db_session, Farm) == 0

    trash_cli.main(["list"])
    listing = capsys.readouterr().out
    assert "in trash" in listing and "test record" in listing and "1 record(s)" in listing
    assert trash_cli.main(["farm", "NOPE"]) == 1
