"""
The phase of each logged operation (pre-planting, planting, post-planting, harvest, post-harvest),
shown on /signals and in the farm summary. The phases come from the Knowledge Base.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from app.clients.base import ClientResult
from app.contracts.knowledge_base import Taxonomy
from app.services.phases import phase_of
from tests.conftest import API_KEY, requires_db

FIXTURE = Path(__file__).parent / "fixtures" / "knowledge_base" / "taxonomy.json"
TAXONOMY = Taxonomy.model_validate(json.loads(FIXTURE.read_text()))
ORDER = ["pre_planting", "planting", "post_planting", "harvest", "post_harvest"]


def test_the_real_taxonomy_carries_the_five_phases_in_order():
    assert [p.id for p in TAXONOMY.phases] == ORDER
    assert TAXONOMY.phase_name("post_planting") == "Post-planting"
    assert TAXONOMY.phase_of_category("harvesting") == "harvest" and TAXONOMY.phase_of_category("other") is None


@pytest.mark.parametrize("kwargs, expected", [
    ({"kb_operation_id": "weeding_1"}, "post_planting"),
    ({"kb_operation_id": "first_fertilizer"}, "post_planting"),
    ({"kb_operation_id": "harvest"}, "harvest"),
    ({"kb_operation_id": "tapping", "crop_id": "rubber"}, "harvest"),
    ({"kb_operation_id": "drying"}, "post_harvest"),
    ({"kb_operation_id": "land_clearing"}, "pre_planting"),
])
def test_an_identified_step_gives_its_phase(kwargs, expected):
    assert phase_of(TAXONOMY, **kwargs) == expected


@pytest.mark.parametrize("logged, expected", [("irrigation", "post_planting"), ("Pruning", "post_planting"), (" Irrigation ", "post_planting")])
def test_a_job_the_knowledge_base_names_individually_uses_its_own_phase(logged, expected):
    assert phase_of(TAXONOMY, signal_category="other", logged_type=logged) == expected


@pytest.mark.parametrize("category, expected", [("planting", "planting"), ("harvesting", "harvest"), ("storage", "post_harvest"), ("land_preparation", "pre_planting")])
def test_otherwise_the_jobs_group_decides(category, expected):
    assert phase_of(TAXONOMY, signal_category=category, logged_type="something the list does not know") == expected


def test_the_most_specific_answer_wins_over_the_group():
    # Logged as a general "other" job, but identified as the weeding step.
    assert phase_of(TAXONOMY, signal_category="other", kb_operation_id="weeding_1") == "post_planting"


def test_no_phase_is_ever_guessed():
    assert phase_of(TAXONOMY, signal_category="other", logged_type="general") is None
    assert phase_of(TAXONOMY, signal_category=None, logged_type=None) is None
    assert phase_of(TAXONOMY, kb_operation_id="not_a_real_step", signal_category="other") is None
    assert phase_of(None, kb_operation_id="harvest", signal_category="harvesting") is None


# ---------------- /signals and the farm summary ----------------

def _cycle_with_four_operations(db_session):
    from tests.test_signals_store import _farm_with_cycle, _operation

    farm, cycle = _farm_with_cycle(db_session, suffix="ph1")
    _operation(db_session, farm, cycle, "planting", "planting", 1, 1)
    _operation(db_session, farm, cycle, "weeding", "weeding", 10, 2)
    _operation(db_session, farm, cycle, "other", "irrigation", 11, 3)
    _operation(db_session, farm, cycle, "harvesting", "harvest", 28, 4)
    _operation(db_session, farm, cycle, "other", "general", 29, 5)
    return farm, cycle


@requires_db
def test_signals_carry_each_operations_phase_and_a_count_per_phase(client, db_session):
    from app.jobs.judge_operations import judge_cycle
    from tests.test_signal_engine import FakeKnowledgeBase

    farm, cycle = _cycle_with_four_operations(db_session)
    judge_cycle(db_session, cycle, FakeKnowledgeBase(), "0.1.0+x")
    db_session.commit()

    body = client.get(f"/farms/{farm.id}/signals", headers={"X-API-Key": API_KEY}).json()

    by_job = {s["logged_type"]: (s["phase"], s["phase_name"]) for s in body["signals"]}
    assert by_job["planting"] == ("planting", "Planting")
    assert by_job["weeding"] == ("post_planting", "Post-planting")
    assert by_job["irrigation"] == ("post_planting", "Post-planting")
    assert by_job["harvest"] == ("harvest", "Harvest")
    assert by_job["general"] == (None, None)
    assert body["phases_available"] is True
    assert body["phase_counts"] == {"planting": 1, "post_planting": 2, "harvest": 1, "none": 1}


@requires_db
def test_when_the_phases_cannot_be_looked_up_the_signals_still_come_back(client, db_session):
    from app.api.routes.farms import knowledge_base
    from app.jobs.judge_operations import judge_cycle
    from app.main import app
    from tests.test_signal_engine import FakeKnowledgeBase

    class Down:
        def taxonomy(self):
            return ClientResult(unavailable_reason="Knowledge Base timed out")

    farm, cycle = _cycle_with_four_operations(db_session)
    judge_cycle(db_session, cycle, FakeKnowledgeBase(), "0.1.0+x")
    db_session.commit()
    app.dependency_overrides[knowledge_base] = Down

    body = client.get(f"/farms/{farm.id}/signals", headers={"X-API-Key": API_KEY}).json()

    assert body["phases_available"] is False and len(body["signals"]) == 5
    assert {s["phase"] for s in body["signals"]} == {None} and body["phase_counts"] == {"none": 5}


def _build(taxonomy, rows):
    import uuid

    from app.services.farm_intelligence import build_intelligence
    from tests.test_farm_intelligence import FakeKb, FakePis

    return build_intelligence(uuid.uuid4(), "Kano", [], [], rows, FakeKb(), FakePis(), date(2026, 10, 23), taxonomy=taxonomy)


def test_the_farm_summary_groups_operations_by_phase_in_order():
    from tests.test_farm_intelligence import cycle, signal

    c = cycle()
    rows = [
        signal(c, "ok", [], date(2026, 8, 20), "planting"),
        signal(c, "warning", [], date(2026, 9, 10), "weeding_1"),
        signal(c, "blocked", [], date(2026, 10, 1), "harvest"),
        signal(c, "unknown", [], date(2026, 10, 2), "weeding_2"),
    ]
    for (sig, op, _), category in zip(rows, ["planting", "weeding", "harvesting", "weeding"]):
        op.signal_category = category

    out = _build(TAXONOMY, rows)

    summary = {p.phase: p for p in out.signals.by_phase}
    assert [p.phase for p in out.signals.by_phase] == ORDER
    assert (summary["planting"].operations, summary["planting"].flagged) == (1, 0)
    assert (summary["post_planting"].operations, summary["post_planting"].warnings, summary["post_planting"].unknown) == (2, 1, 1)
    assert (summary["harvest"].operations, summary["harvest"].flagged) == (1, 1)
    assert summary["pre_planting"].operations == 0 and summary["post_harvest"].operations == 0


def test_without_the_phases_the_summary_has_an_empty_breakdown_not_an_error():
    assert _build(None, []).signals.by_phase == []
