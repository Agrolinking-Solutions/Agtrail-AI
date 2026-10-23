"""
Works out which Knowledge Base step a logged operation is.

AgTrail says "weeding"; the Knowledge Base's rules are written for "weeding_1" and
"weeding_2". Several steps can share one category, so a guess here would produce
false warnings. A step is therefore named only when it can be told for certain,
by one of these rules. Everything else stays unidentified, with the reason.

  1. The crop has exactly one step in the category: the first operation logged in
     that category is that step.
  2. The steps are a numbered series (first_fertilizer, second_fertilizer or
     weeding_1, weeding_2): the 1st operation logged is the 1st step, the 2nd the
     2nd. More operations than steps cannot be told apart.
  3. Several steps, not a series, but one is named exactly like the category
     (planting, within planting): the first operation logged is that step.
  Anything else, such as harvest_fresh against harvest, is left unidentified.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.contracts.knowledge_base import CropStep, CropSteps

ORDINAL_WORDS = ("first", "second", "third", "fourth", "fifth")


@dataclass(frozen=True)
class LoggedOperation:
    key: str
    signal_category: Optional[str]
    operation_date: date


@dataclass(frozen=True)
class StepAssignment:
    """kb_operation_id is set, or `status` says why not.
    not_applicable: the crop's rules have no step of this kind (irrigation, say).
    unknown: the step could not be told for certain."""

    kb_operation_id: Optional[str] = None
    status: Optional[str] = None
    reason: Optional[str] = None


def _series_stem(operation_id: str) -> str:
    stem = re.sub(r"_\d+$", "", operation_id)
    return "_".join(part for part in stem.split("_") if part not in ORDINAL_WORDS)


def _is_numbered_series(steps: list[CropStep]) -> bool:
    return len({_series_stem(step.operation_id) for step in steps}) == 1


def assign_steps(crop_steps: CropSteps, operations: list[LoggedOperation]) -> dict[str, StepAssignment]:
    """Keyed by LoggedOperation.key. Operations are taken in date order, ties in the order given."""
    result: dict[str, StepAssignment] = {}
    seen_in_category: dict[str, int] = {}

    for operation in sorted(operations, key=lambda op: op.operation_date):  # stable
        category = operation.signal_category
        if not category or category == "other":
            # "other" is the catch-all, so it says nothing about which step this is.
            result[operation.key] = StepAssignment(status="unknown", reason="This kind of operation has no specific category, so no step can be named.")
            continue
        candidates = crop_steps.steps_in_category(category)
        if not candidates:
            result[operation.key] = StepAssignment(
                status="not_applicable", reason=f"The rules for {crop_steps.crop_id} have no '{category}' step."
            )
            continue

        position = seen_in_category.get(category, 0)
        seen_in_category[category] = position + 1
        step = _pick(candidates, category, position)
        if step is None:
            options = ", ".join(c.operation_id for c in candidates)
            result[operation.key] = StepAssignment(
                status="unknown", reason=f"Could be any of: {options}. Not guessed."
            )
        else:
            result[operation.key] = StepAssignment(kb_operation_id=step.operation_id)
    return result


def _pick(candidates: list[CropStep], category: str, position: int) -> Optional[CropStep]:
    if len(candidates) == 1:
        return candidates[0] if position == 0 else None
    if _is_numbered_series(candidates):
        return candidates[position] if position < len(candidates) else None
    if position == 0:
        return next((c for c in candidates if c.operation_id == category), None)
    return None
