"""
Step identification and the signal engine. The Knowledge Base is replaced by a
scripted stand-in built from its real (captured) responses, so nothing here
touches the network or a database.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from app.clients.base import ClientResult
from app.contracts.knowledge_base import CropSteps, ValidationReport
from app.services.operation_steps import LoggedOperation, assign_steps
from app.services.signal_engine import check_cycle

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge_base"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


SORGHUM = CropSteps.model_validate(fixture("crop_steps_sorghum"))
MAIZE = CropSteps.model_validate(fixture("crop_steps_maize"))


def op(key: str, category, day: int) -> LoggedOperation:
    return LoggedOperation(key=key, signal_category=category, operation_date=date(2026, 6, day))


def ids(assignments) -> dict:
    return {k: v.kb_operation_id for k, v in assignments.items()}


# ---------------- which step is it ----------------

def test_a_single_step_category_names_the_first_and_leaves_repeats_unidentified():
    result = assign_steps(SORGHUM, [op("a", "planting", 1), op("b", "planting", 3)])

    assert ids(result) == {"a": "planting", "b": None}
    assert result["b"].status == "unknown"


def test_a_numbered_series_is_matched_by_order_of_logging():
    result = assign_steps(
        SORGHUM,
        [op("w2", "weeding", 20), op("w1", "weeding", 5), op("f1", "fertilizer_application", 6), op("f2", "fertilizer_application", 25)],
    )

    assert ids(result) == {"w1": "weeding_1", "w2": "weeding_2", "f1": "first_fertilizer", "f2": "second_fertilizer"}


def test_more_operations_than_steps_in_a_series_is_not_guessed():
    result = assign_steps(SORGHUM, [op("a", "weeding", 1), op("b", "weeding", 2), op("c", "weeding", 3)])

    assert ids(result)["c"] is None and result["c"].status == "unknown"


def test_a_step_named_like_its_category_is_the_first_one_logged():
    result = assign_steps(SORGHUM, [op("a", "land_preparation", 1), op("b", "land_preparation", 2)])

    assert ids(result) == {"a": "land_preparation", "b": None}


def test_steps_that_are_alternatives_are_never_guessed():
    # Maize has harvest_fresh and harvest. Nothing says which one was logged.
    result = assign_steps(MAIZE, [op("a", "harvesting", 1)])

    assert result["a"].status == "unknown" and "harvest_fresh" in result["a"].reason and "harvest" in result["a"].reason


def test_the_catch_all_category_is_never_matched_even_if_the_crop_has_a_step_in_it():
    assert any(s.signal_category == "other" for s in MAIZE.steps)  # pest_scouting

    assert assign_steps(MAIZE, [op("a", "other", 1)])["a"].status == "unknown"


def test_a_kind_of_operation_the_crop_has_no_step_for_is_not_applicable():
    result = assign_steps(SORGHUM, [op("a", "pest_control", 1)])

    assert result["a"].status == "not_applicable"


def test_an_operation_with_no_category_is_unknown():
    assert assign_steps(SORGHUM, [op("a", None, 1)])["a"].status == "unknown"


# ---------------- the engine ----------------

class FakeKnowledgeBase:
    def __init__(self, steps=SORGHUM, reports=None, steps_down=False, down_for=()):
        self.steps, self.reports, self.steps_down, self.down_for = steps, reports or {}, steps_down, set(down_for)
        self.calls = []

    def crop_steps(self, crop_id):
        if self.steps_down:
            return ClientResult(unavailable_reason="Knowledge Base timed out")
        return ClientResult(value=self.steps)

    def validate(self, crop_id, history, new_operation, region=None):
        self.calls.append((list(history), dict(new_operation)))
        if new_operation["operation_id"] in self.down_for:
            return ClientResult(unavailable_reason="Knowledge Base timed out")
        raw = self.reports.get(new_operation["operation_id"]) or fixture("validate_clean")
        return ClientResult(value=ValidationReport.model_validate({**raw, "operation_id": new_operation["operation_id"], "date": new_operation["date"]}))


CYCLE = [
    op("clear", "land_preparation", 1),
    op("plant", "planting", 15),
    op("weed", "weeding", 29),
]


def test_each_operation_is_judged_only_against_what_came_before_it():
    kb = FakeKnowledgeBase()

    verdicts = check_cycle("sorghum", CYCLE, kb)

    assert [v.status for v in verdicts] == ["ok", "ok", "ok"]
    assert [v.kb_operation_id for v in verdicts] == ["land_preparation", "planting", "weeding_1"]
    assert [[h["operation_id"] for h in call[0]] for call in kb.calls] == [[], ["land_preparation"], ["land_preparation", "planting"]]


def test_a_block_from_the_knowledge_base_is_a_block_with_its_rule_and_message():
    kb = FakeKnowledgeBase(reports={"harvest": fixture("validate_blocked")})
    cycle = [op("plant", "planting", 1), op("harvest", "harvesting", 20)]

    verdicts = check_cycle("sorghum", cycle, kb)

    assert verdicts[1].status == "blocked"
    assert [i.rule_id for i in verdicts[1].issues][0] == "sorghum_r8"
    assert "85 days" in verdicts[1].issues[0].message


def test_warnings_alone_are_a_warning():
    blocked = fixture("validate_blocked")
    warning_only = {**blocked, "blocked": False, "blocking_issues": [], "issues": blocked["warnings"]}
    kb = FakeKnowledgeBase(reports={"harvest": warning_only})

    verdict = check_cycle("sorghum", [op("plant", "planting", 1), op("harvest", "harvesting", 20)], kb)[1]

    assert verdict.status == "warning" and verdict.issues[0].rule_id == "sorghum_r8b"


def missing(step: str) -> dict:
    issue = {"rule_id": "x", "severity": "block", "message": f"'planting' requires '{step}' to be logged first, and no record of it exists yet."}
    return {"crop_id": "sorghum", "operation_id": "planting", "date": "2026-06-15", "blocked": True, "issues": [issue], "warnings": [], "blocking_issues": [issue]}


def test_a_missing_earlier_step_is_a_block_when_nothing_logged_could_be_it():
    kb = FakeKnowledgeBase(reports={"planting": missing("land_preparation")})

    verdict = check_cycle("sorghum", [op("plant", "planting", 15)], kb)[0]

    assert verdict.status == "blocked"


def test_a_missing_earlier_step_is_unknown_when_a_logged_operation_might_be_it():
    # Only "land clearing or preparation" was logged, once. It was taken as land_preparation,
    # so the rule asking for land_clearing cannot honestly be called broken.
    kb = FakeKnowledgeBase(reports={"land_preparation": missing("land_clearing")})

    verdict = check_cycle("sorghum", [op("prep", "land_preparation", 1)], kb)[0]

    assert verdict.status == "unknown" and "could not identify" in verdict.reason


def test_a_real_block_is_kept_even_when_a_missing_step_is_uncertain():
    both = fixture("validate_blocked")
    both["blocking_issues"] = both["blocking_issues"] + missing("land_clearing")["blocking_issues"]
    kb = FakeKnowledgeBase(reports={"harvest": both})

    verdict = check_cycle("sorghum", [op("prep", "land_preparation", 1), op("harvest", "harvesting", 20)], kb)[1]

    assert verdict.status == "blocked"
    assert [i.rule_id for i in verdict.issues if i.severity == "block"] == ["sorghum_r8"]


def test_unidentified_and_not_applicable_operations_are_not_sent_to_the_knowledge_base():
    kb = FakeKnowledgeBase()
    cycle = [op("a", "planting", 1), op("b", "pest_control", 2), op("c", "other", 3), op("d", "weeding", 20)]

    verdicts = check_cycle("sorghum", cycle, kb)

    assert [v.status for v in verdicts] == ["ok", "not_applicable", "unknown", "ok"]
    assert [call[1]["operation_id"] for call in kb.calls] == ["planting", "weeding_1"]
    assert [h["operation_id"] for h in kb.calls[1][0]] == ["planting"]  # the unidentified ones are not in the history


def test_when_the_knowledge_base_cannot_be_reached_nothing_is_judged():
    verdicts = check_cycle("sorghum", CYCLE, FakeKnowledgeBase(steps_down=True))

    assert [v.status for v in verdicts] == ["unknown"] * 3
    assert all("Knowledge Base unavailable" in v.reason for v in verdicts)


def test_one_failed_call_leaves_that_operation_unknown_and_the_rest_judged():
    verdicts = check_cycle("sorghum", CYCLE, FakeKnowledgeBase(down_for={"planting"}))

    assert [v.status for v in verdicts] == ["ok", "unknown", "ok"]


def test_operations_are_judged_in_date_order_whatever_order_they_arrive_in():
    verdicts = check_cycle("sorghum", list(reversed(CYCLE)), FakeKnowledgeBase())

    assert [v.key for v in verdicts] == ["clear", "plant", "weed"]


def test_the_cycles_planting_date_stands_in_for_a_planting_that_was_never_logged():
    kb = FakeKnowledgeBase()

    verdicts = check_cycle("sorghum", [op("weed", "weeding", 29)], kb, planted_on=date(2026, 6, 15))

    assert [v.key for v in verdicts] == ["weed"]  # the stand-in gets no verdict of its own
    assert kb.calls[0][0] == [{"operation_id": "planting", "date": "2026-06-15"}]


def test_a_logged_planting_is_used_instead_of_the_cycles_date():
    kb = FakeKnowledgeBase()

    check_cycle("sorghum", [op("plant", "planting", 14), op("weed", "weeding", 29)], kb, planted_on=date(2026, 6, 1))

    assert kb.calls[1][0] == [{"operation_id": "planting", "date": "2026-06-14"}]


def test_the_planting_date_is_optional():
    kb = FakeKnowledgeBase()

    check_cycle("sorghum", [op("weed", "weeding", 29)], kb)

    assert kb.calls[0][0] == []
