"""
Stored verdicts, the judging job and the /signals endpoint. The Knowledge Base is a
scripted stand-in, so only the test database is needed.
"""

from datetime import date, timedelta

from sqlalchemy import select

from tests.conftest import API_KEY, requires_db
from tests.test_signal_engine import FakeKnowledgeBase, fixture

pytestmark = requires_db


def _farm_with_cycle(db_session, crop_id="sorghum", planted=date(2026, 6, 15), suffix="1"):
    from app.models.crop_cycle import CropCycle
    from app.models.farm import Farm
    from app.models.farmer import Farmer

    farmer = Farmer(full_name="Test Farmer", phone=f"0800000{suffix}", state="Kano", lga="Kano Municipal")
    db_session.add(farmer)
    db_session.flush()
    farm = Farm(farmer_id=farmer.id, name="Test Farm", agtrail_farm_id=f"AGT-SIG-{suffix}")
    db_session.add(farm)
    db_session.flush()
    cycle = CropCycle(
        farm_id=farm.id, agtrail_cycle_id=f"CYC-{suffix}", crop_raw="Sorghum", crop_id=crop_id,
        planted_date=planted, status="active",
    )
    db_session.add(cycle)
    db_session.flush()
    return farm, cycle


def _operation(db_session, farm, cycle, category, logged, day, n):
    from app.models.farm_operation import FarmOperation

    op = FarmOperation(
        farm_id=farm.id, crop_cycle_id=cycle.id, agtrail_operation_id=f"OP-{cycle.agtrail_cycle_id}-{n}",
        logged_type_raw=logged, signal_category=category, operation_date=date(2026, 6, day),
    )
    db_session.add(op)
    db_session.flush()
    return op


def _signals(db_session, cycle):
    from app.models.operation_signal import OperationSignal

    return db_session.execute(select(OperationSignal).where(OperationSignal.crop_cycle_id == cycle.id)).scalars().all()


def test_verdicts_are_stored_and_the_identified_step_is_written_back(db_session):
    from app.jobs.judge_operations import judge_cycle

    farm, cycle = _farm_with_cycle(db_session)
    plant = _operation(db_session, farm, cycle, "planting", "planting", 15, 1)
    weed = _operation(db_session, farm, cycle, "weeding", "weeding", 29, 2)
    other = _operation(db_session, farm, cycle, "other", "general", 30, 3)

    assert judge_cycle(db_session, cycle, FakeKnowledgeBase(), "0.1.0+abc") == "judged"
    db_session.commit()

    by_op = {s.operation_id: s for s in _signals(db_session, cycle)}
    assert by_op[plant.id].status == "ok" and by_op[weed.id].status == "ok"
    assert by_op[other.id].status == "unknown" and "no specific category" in by_op[other.id].reason
    assert {s.kb_version for s in by_op.values()} == {"0.1.0+abc"}
    assert (plant.kb_operation_id, weed.kb_operation_id, other.kb_operation_id) == ("planting", "weeding_1", None)


def test_running_again_updates_the_same_rows_when_the_rules_change(db_session):
    from app.jobs.judge_operations import judge_cycle

    farm, cycle = _farm_with_cycle(db_session, suffix="2")
    plant = _operation(db_session, farm, cycle, "planting", "planting", 1, 1)
    harvest = _operation(db_session, farm, cycle, "harvesting", "harvest", 20, 2)

    judge_cycle(db_session, cycle, FakeKnowledgeBase(), "v1")
    db_session.commit()
    assert {s.status for s in _signals(db_session, cycle)} == {"ok"}

    strict = FakeKnowledgeBase(reports={"harvest": fixture("validate_blocked")})
    judge_cycle(db_session, cycle, strict, "v2")
    db_session.commit()

    rows = {s.operation_id: s for s in _signals(db_session, cycle)}
    assert len(rows) == 2
    assert rows[harvest.id].status == "blocked" and rows[harvest.id].kb_version == "v2"
    assert rows[harvest.id].issues[0]["rule_id"] == "sorghum_r8"
    assert rows[plant.id].status == "ok"


def test_an_unreachable_knowledge_base_keeps_the_old_verdicts(db_session):
    from app.jobs.judge_operations import judge_cycle

    farm, cycle = _farm_with_cycle(db_session, suffix="3")
    _operation(db_session, farm, cycle, "planting", "planting", 1, 1)
    judge_cycle(db_session, cycle, FakeKnowledgeBase(), "v1")
    db_session.commit()

    outcome = judge_cycle(db_session, cycle, FakeKnowledgeBase(steps_down=True), None)
    db_session.commit()

    assert outcome == "skipped"
    rows = _signals(db_session, cycle)
    assert [(s.status, s.kb_version) for s in rows] == [("ok", "v1")]


def test_judge_all_only_judges_cycles_whose_crop_has_rules(db_session, monkeypatch):
    import app.jobs.judge_operations as job

    farm, cycle = _farm_with_cycle(db_session, suffix="4")
    _operation(db_session, farm, cycle, "planting", "planting", 1, 1)
    farm2, no_rules = _farm_with_cycle(db_session, crop_id=None, suffix="5")
    _operation(db_session, farm2, no_rules, "planting", "planting", 1, 1)
    db_session.commit()

    class Versioned(FakeKnowledgeBase):
        def version(self):
            from app.clients.base import ClientResult
            from app.contracts.knowledge_base import KnowledgeBaseVersion

            return ClientResult(value=KnowledgeBaseVersion(kb_version="0.1.0+test"))

    monkeypatch.setattr(job, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    summary = job.judge_all(Versioned())

    assert summary == {"cycles_judged": 1, "cycles_skipped": 0}
    assert len(_signals(db_session, cycle)) == 1 and _signals(db_session, no_rules) == []


def test_the_endpoint_lists_signals_newest_first_and_calls_a_block_flagged(client, db_session):
    from app.jobs.judge_operations import judge_cycle

    farm, cycle = _farm_with_cycle(db_session, suffix="6")
    _operation(db_session, farm, cycle, "planting", "planting", 1, 1)
    _operation(db_session, farm, cycle, "harvesting", "harvest", 20, 2)
    _operation(db_session, farm, cycle, "other", "general", 21, 3)
    judge_cycle(db_session, cycle, FakeKnowledgeBase(reports={"harvest": fixture("validate_blocked")}), "0.1.0+abc")
    db_session.commit()

    response = client.get(f"/farms/{farm.id}/signals", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["provenance"] == "inferred" and body["kb_version"] == "0.1.0+abc"
    assert [s["logged_type"] for s in body["signals"]] == ["general", "harvest", "planting"]
    assert [s["status"] for s in body["signals"]] == ["unknown", "flagged", "ok"]
    assert body["counts"] == {"unknown": 1, "flagged": 1, "ok": 1}
    flagged = body["signals"][1]
    assert flagged["kb_operation_id"] == "harvest" and flagged["issues"][0]["rule_id"] == "sorghum_r8"


def test_a_farm_never_judged_has_an_empty_list_and_an_unknown_farm_is_404(client, db_session):
    import uuid

    farm, _ = _farm_with_cycle(db_session, suffix="7")
    db_session.commit()

    empty = client.get(f"/farms/{farm.id}/signals", headers={"X-API-Key": API_KEY}).json()
    assert empty["signals"] == [] and empty["counts"] == {} and empty["kb_version"] is None
    assert client.get(f"/farms/{uuid.uuid4()}/signals", headers={"X-API-Key": API_KEY}).status_code == 404
    assert client.get(f"/farms/{farm.id}/signals").status_code in (401, 403)


def test_the_daily_ingest_looks_back_20_days_for_known_farms_and_90_for_new_ones(db_session, monkeypatch):
    from geoalchemy2 import WKTElement

    import app.jobs.ingest_ndvi as job
    from app.models.farm import Farm
    from app.models.farmer import Farmer
    from app.models.ndvi import FarmNdviScene
    from app.services.sentinel import NdviScene

    farmer = Farmer(full_name="Test Farmer", phone="08000099", state="Oyo", lga="Ibadan North")
    db_session.add(farmer)
    db_session.flush()
    square = WKTElement("POLYGON((7.9 4.7, 7.9005 4.7, 7.9005 4.7005, 7.9 4.7005, 7.9 4.7))", srid=4326)
    old = Farm(farmer_id=farmer.id, name="Known", agtrail_farm_id="AGT-OLD", boundary_mapped=True, boundary=square)
    new = Farm(farmer_id=farmer.id, name="New", agtrail_farm_id="AGT-NEW", boundary_mapped=True, boundary=square)
    db_session.add_all([old, new])
    db_session.flush()
    db_session.add(FarmNdviScene(farm_id=old.id, scene_date=date(2026, 9, 1), ndvi_mean=0.5, polygon_pixels=10, clear_fraction=1.0))
    db_session.commit()

    asked = []

    def fake_fetch(boundary, start, end, token):
        asked.append((end - start).days)
        return [NdviScene(end, 0.5, 10, 1.0)]

    monkeypatch.setattr(job, "get_access_token", lambda: "token")
    monkeypatch.setattr(job, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(job, "fetch_farm_scenes", fake_fetch)
    monkeypatch.setattr(db_session, "close", lambda: None)

    summary = job.ingest_scenes()

    assert sorted(asked) == [job.RECENT_DAYS, job.FIRST_RUN_DAYS]
    assert summary["farms"] == 2 and summary["scenes_written"] == 2
