"""Crop cycles and logged operations: saved from the webhook, and by the backfill."""

import copy
from datetime import date

from sqlalchemy import func, select, text

from tests.conftest import BACKEND_DIR, load_payload, post_event, requires_db

pytestmark = requires_db


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def _image_payload(
    event_id,
    *,
    op_type="planting",
    op_id=None,
    op_date=None,
    captured_at="2026-08-08T11:55:00Z",
    cycle_id="CYC-TEST-001",
    crop="Rice",
):
    payload = copy.deepcopy(load_payload("test_payload.json"))
    payload["event_id"] = event_id
    payload["crop_cycle"]["agtrail_cycle_id"] = cycle_id
    payload["crop_cycle"]["crop"] = crop
    payload["image"]["captured_at"] = captured_at
    if op_type is None:
        payload.pop("operation_context")
    else:
        context = {"logged_operation_type": op_type}
        if op_id:
            context["agtrail_operation_id"] = op_id
        if op_date is not None:
            context["operation_date"] = op_date
        payload["operation_context"] = context
    return payload


def test_crop_cycle_event_saves_the_cycle(client, db_session):
    from app.models import CropCycle

    response = post_event(client, load_payload("test_payload_crop_cycle.json"))

    assert response.status_code == 200, response.text
    cycle = db_session.execute(select(CropCycle)).scalar_one()
    assert cycle.agtrail_cycle_id == "CYC-TEST-002"
    assert cycle.crop_raw == "Maize"
    assert cycle.crop_canonical == "maize"
    assert cycle.crop_id == "maize"
    assert cycle.variety == "Oba Super 2"
    assert cycle.planted_date == date(2026, 8, 1)
    assert cycle.status == "planted"
    assert cycle.source == "agtrail"


def test_replaying_a_crop_cycle_event_makes_one_row(client, db_session):
    from app.models import CropCycle

    payload = load_payload("test_payload_crop_cycle.json")
    post_event(client, payload)
    # The same event again is a no-op.
    assert post_event(client, payload).json()["status"] == "already_processed"
    # A new event for the same cycle updates it instead of adding a second.
    later = copy.deepcopy(payload)
    later["event_id"] = "evt_crop_cycle_status_change"
    later["crop_cycle"]["status"] = "growing"
    assert post_event(client, later).status_code == 200

    cycle = db_session.execute(select(CropCycle)).scalar_one()
    assert cycle.status == "growing"


def test_monitoring_image_saves_cycle_operation_and_links_the_photo(client, db_session):
    from app.models import CropCycle, FarmImage, FarmOperation

    response = post_event(client, load_payload("test_payload.json"))

    assert response.status_code == 200, response.text
    cycle = db_session.execute(select(CropCycle)).scalar_one()
    assert cycle.crop_id == "rice"
    operation = db_session.execute(select(FarmOperation)).scalar_one()
    assert operation.crop_cycle_id == cycle.id
    assert operation.logged_type_raw == "planting"
    assert operation.signal_category == "planting"
    assert operation.operation_date == date(2026, 8, 8)
    image = db_session.execute(select(FarmImage)).scalar_one()
    assert image.farm_operation_id == operation.id


def test_photos_of_one_operation_share_a_row_dated_by_the_earliest(client, db_session):
    from app.models import FarmImage, FarmOperation

    post_event(client, _image_payload("evt_a", op_id="OP-1", captured_at="2026-08-08T11:55:00Z"))
    post_event(client, _image_payload("evt_b", op_id="OP-1", captured_at="2026-08-06T10:00:00Z"))

    operation = db_session.execute(select(FarmOperation)).scalar_one()
    assert operation.operation_date == date(2026, 8, 6)
    images = db_session.execute(select(FarmImage)).scalars().all()
    assert len(images) == 2
    assert {image.farm_operation_id for image in images} == {operation.id}


def test_photos_without_an_operation_id_each_get_their_own_row(client, db_session):
    from app.models import FarmOperation

    post_event(client, _image_payload("evt_a"))
    post_event(client, _image_payload("evt_b"))

    assert _count(db_session, FarmOperation) == 2


def test_operation_date_uses_nigerian_time(client, db_session):
    from app.models import FarmOperation

    # 23:30 UTC is already the next morning in Nigeria.
    post_event(client, _image_payload("evt_late", captured_at="2026-08-08T23:30:00Z"))

    operation = db_session.execute(select(FarmOperation)).scalar_one()
    assert operation.operation_date == date(2026, 8, 9)


def test_agtrail_names_map_to_the_shared_categories(client, db_session):
    from app.models import FarmOperation

    post_event(client, _image_payload("evt_fert", op_type="fertilizer"))
    post_event(client, _image_payload("evt_irr", op_type="Irrigation "))

    categories = {
        op.logged_type_raw: op.signal_category
        for op in db_session.execute(select(FarmOperation)).scalars()
    }
    assert categories == {"fertilizer": "fertilizer_application", "Irrigation ": "other"}


def test_unknown_operation_type_is_saved_without_a_category(client, db_session):
    from app.models import FarmOperation

    post_event(client, _image_payload("evt_new", op_type="spraying_new_thing"))

    operation = db_session.execute(select(FarmOperation)).scalar_one()
    assert operation.logged_type_raw == "spraying_new_thing"
    assert operation.signal_category is None


def test_photo_without_an_operation_makes_no_operation(client, db_session):
    from app.models import CropCycle, FarmImage, FarmOperation

    post_event(client, _image_payload("evt_plain", op_type=None))

    assert _count(db_session, CropCycle) == 1
    assert _count(db_session, FarmOperation) == 0
    assert db_session.execute(select(FarmImage)).scalar_one().farm_operation_id is None


def test_crop_text_is_cleaned_up_through_the_aliases(db_session):
    from app.services.farm_records import resolve_crop

    assert resolve_crop(db_session, "MAIZE") == ("maize", "maize")
    assert resolve_crop(db_session, "  Soya   Beans ") == ("soybean", "soybean")
    assert resolve_crop(db_session, "not a known crop") == (None, None)


def test_backfill_rebuilds_records_from_the_stored_payloads(client, db_session):
    from app.jobs.backfill_operations import backfill_operations
    from app.models import CropCycle, FarmImage, FarmOperation

    post_event(client, _image_payload("evt_a", op_id="OP-1"))
    post_event(client, _image_payload("evt_b", op_type="spraying_new_thing", crop="Mystery Crop", cycle_id="CYC-2"))
    # Simulate photos stored before the new tables existed.
    db_session.execute(text("DELETE FROM farm_operations"))
    db_session.execute(text("DELETE FROM crop_cycles"))
    db_session.commit()
    assert all(image.farm_operation_id is None for image in db_session.execute(select(FarmImage)).scalars())

    summary = backfill_operations()

    assert summary["images_linked"] == 2
    assert summary["cycles_created"] == 2
    assert summary["operations_created"] == 2
    assert summary["unmapped_operation_types"] == {"spraying_new_thing": 1}
    assert summary["crops_without_kb_file"] == {"mystery crop": 1}
    db_session.expire_all()
    assert _count(db_session, CropCycle) == 2
    assert _count(db_session, FarmOperation) == 2

    # Running it again changes nothing.
    again = backfill_operations()
    assert again["images_considered"] == 0
    assert again["cycles_created"] == 0
    assert again["operations_created"] == 0


def test_migration_can_be_undone_and_redone(migrated_db):
    from alembic import command
    from alembic.config import Config

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))

    command.downgrade(config, "c3d4e5f6a7b8")
    with migrated_db.connect() as conn:
        tables = set(conn.scalars(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )))
    assert not tables & {"crop_cycles", "farm_operations", "operation_type_aliases", "crop_aliases"}

    command.upgrade(config, "head")
    with migrated_db.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM operation_type_aliases")) > 0
        assert conn.scalar(text("SELECT count(*) FROM crop_aliases")) > 0


def test_the_date_agtrail_logged_beats_the_photo_date(client, db_session):
    from app.models import FarmOperation

    # Logged late: the photo was taken on 8 Aug, the work happened on 1 Aug.
    response = post_event(client, _image_payload("evt_late_log", op_date="2026-08-01"))

    assert response.status_code == 200, response.text
    assert db_session.execute(select(FarmOperation)).scalar_one().operation_date == date(2026, 8, 1)


def test_operation_date_may_be_a_full_timestamp(client, db_session):
    from app.models import FarmOperation

    response = post_event(client, _image_payload("evt_stamp", op_date="2026-08-01T09:30:00.000Z"))

    assert response.status_code == 200, response.text
    assert db_session.execute(select(FarmOperation)).scalar_one().operation_date == date(2026, 8, 1)


def test_an_unreadable_operation_date_never_rejects_the_event(client, db_session):
    from app.models import FarmOperation

    for index, bad in enumerate(["not a date", "", 12345, {"a": 1}]):
        response = post_event(client, _image_payload(f"evt_bad_{index}", op_date=bad))
        assert response.status_code == 200, (bad, response.text)

    dates = {op.operation_date for op in db_session.execute(select(FarmOperation)).scalars()}
    assert dates == {date(2026, 8, 8)}  # fell back to the photo's date


def test_a_later_event_with_the_real_date_corrects_the_operation(client, db_session):
    from app.models import FarmOperation

    post_event(client, _image_payload("evt_first", op_id="OP-9", captured_at="2026-08-08T11:55:00Z"))
    post_event(client, _image_payload("evt_second", op_id="OP-9", op_date="2026-08-03", captured_at="2026-08-08T12:10:00Z"))

    operation = db_session.execute(select(FarmOperation)).scalar_one()
    assert operation.operation_date == date(2026, 8, 3)


def test_backfill_keeps_the_logged_date_from_the_stored_payload(client, db_session):
    from app.jobs.backfill_operations import backfill_operations
    from app.models import FarmOperation

    post_event(client, _image_payload("evt_keep", op_id="OP-5", op_date="2026-08-02"))
    db_session.execute(text("DELETE FROM farm_operations"))
    db_session.execute(text("DELETE FROM crop_cycles"))
    db_session.commit()

    backfill_operations()

    db_session.expire_all()
    assert db_session.execute(select(FarmOperation)).scalar_one().operation_date == date(2026, 8, 2)


def test_soybean_spellings_reach_the_soybean_rule_file(db_session):
    from app.services.farm_records import resolve_crop

    for text in ("Soyabean", "Soybeans", "soya beans", "SOYBEAN "):
        assert resolve_crop(db_session, text) == ("soybean", "soybean"), text


def test_crop_text_that_is_ambiguous_stays_unmatched(db_session):
    from app.services.farm_records import resolve_crop

    # 'Beans' could be cowpea or another bean; 'Maize and Cassava' is two crops. Never guess.
    assert resolve_crop(db_session, "Beans")[1] is None
    assert resolve_crop(db_session, "Maize and Cassava")[1] is None


def test_a_cycle_stored_before_the_fix_is_matched_by_the_migration_sql(db_session):
    import uuid
    from datetime import date

    from sqlalchemy import select, text

    from app.models import CropCycle, Farm, Farmer

    farmer = Farmer(full_name="T", phone="1", state="Oyo", lga="x", agtrail_farmer_id="t1")
    db_session.add(farmer); db_session.flush()
    farm = Farm(farmer_id=farmer.id, name="f", agtrail_farm_id="t-farm")
    db_session.add(farm); db_session.flush()
    cycle = CropCycle(farm_id=farm.id, agtrail_cycle_id="t-cycle", crop_raw=" Soyabean ", planted_date=date(2026, 9, 1), status="active")
    db_session.add(cycle); db_session.commit()

    db_session.execute(text(
        "UPDATE crop_cycles c SET crop_id = a.kb_crop_id, crop_canonical = a.canonical_name FROM crop_aliases a "
        "WHERE c.crop_id IS NULL AND a.kb_crop_id IS NOT NULL "
        "AND lower(regexp_replace(trim(c.crop_raw), '\\s+', ' ', 'g')) = a.alias"
    ))
    db_session.commit(); db_session.expire_all()
    assert db_session.get(CropCycle, cycle.id).crop_id == "soybean"
