"""Rebuilding crop cycles from the farm.crop_cycle.created events stored before cycles were saved."""

import copy

from sqlalchemy import select, text

from tests.conftest import load_payload, post_event, requires_db

pytestmark = requires_db


def _cycle_event(event_id, cycle_id, crop="Soyabean", farm_id=None):
    p = copy.deepcopy(load_payload("test_payload_crop_cycle.json"))
    p["event_id"] = event_id
    p["crop_cycle"]["agtrail_cycle_id"] = cycle_id
    p["crop_cycle"]["crop"] = crop
    if farm_id:
        p["farm"]["agtrail_farm_id"] = farm_id
    return p


def test_cycles_are_rebuilt_from_stored_events_and_a_second_run_adds_nothing(client, db_session):
    from app.jobs.backfill_crop_cycles import backfill_crop_cycles
    from app.models import CropCycle

    post_event(client, _cycle_event("evt-1", "CYC-A", crop="Soyabean"))
    post_event(client, _cycle_event("evt-2", "CYC-B", crop="Maize"))
    # Simulate the old days: the event was accepted but no cycle record was made.
    db_session.execute(text("DELETE FROM farm_operations"))
    db_session.execute(text("DELETE FROM crop_cycles"))
    db_session.commit()

    first = backfill_crop_cycles()
    second = backfill_crop_cycles()

    db_session.expire_all()
    cycles = {c.agtrail_cycle_id: c for c in db_session.execute(select(CropCycle)).scalars()}
    assert first["cycles_created"] == 2 and set(cycles) == {"CYC-A", "CYC-B"}
    assert cycles["CYC-A"].crop_id == "soybean" and cycles["CYC-B"].crop_id == "maize"
    assert second["cycles_created"] == 0 and second["cycles_updated"] == 2


def test_an_event_for_a_farm_we_do_not_have_is_skipped_not_invented(client, db_session):
    from app.jobs.backfill_crop_cycles import backfill_crop_cycles
    from app.models import Farm

    post_event(client, _cycle_event("evt-3", "CYC-C"))
    db_session.execute(text("DELETE FROM farm_operations"))
    db_session.execute(text("DELETE FROM crop_cycles"))
    # The stored event names a farm this hub has never heard of.
    db_session.execute(text("UPDATE agtrail_sync_log SET payload = jsonb_set(payload, '{farm,agtrail_farm_id}', '\"NO-SUCH-FARM\"')"))
    db_session.commit()

    summary = backfill_crop_cycles()

    assert summary["skipped_no_farm"] == 1 and summary["cycles_created"] == 0
    assert db_session.scalar(text("select count(*) from crop_cycles")) == 0
