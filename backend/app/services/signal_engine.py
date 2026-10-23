"""
Judges a crop cycle's logged operations against the Knowledge Base's rules.

Each operation is judged against only what was logged before it, which is how the
Knowledge Base's rules are written. The verdict is one of:

  ok / warning / blocked   the Knowledge Base's answer, with its rule ids and messages
  not_applicable           the crop's rules have no step of this kind (irrigation)
  unknown                  no judgement is made, and the reason says why: the step
                           could not be identified for certain, the rule depends on
                           something we could not identify, or the Knowledge Base
                           was unreachable

Unknown is never turned into a guess. A wrong warning costs more trust than a
missing one.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Protocol

from app.clients.base import ClientResult
from app.contracts.knowledge_base import CropSteps, RuleIssue, ValidationReport
from app.services.operation_steps import LoggedOperation, StepAssignment, assign_steps

# Stands in for a planting operation that was never logged, see check_cycle.
CYCLE_PLANTING_KEY = "cycle:planting"

# The Knowledge Base's wording when an earlier step has not been logged.
MISSING_STEP = re.compile(r"requires '([^']+)' to be logged first")


TIMING = re.compile(
    r"logged (\d+) day\(s\) after '([^']+)'.*?"
    r"(?:minimum is (\d+)|maximum is (\d+)|inside the flagged (\d+)-(\d+) day window)",
    re.S,
)


def _name(operation_id: str) -> str:
    return operation_id.replace("_", " ")


def classify_issue(issue: RuleIssue) -> tuple[str, Optional[str]]:
    """What kind of problem a Knowledge Base issue is, and a plain one-line summary
    where the message follows a fixed pattern. The kinds matter because they mean
    different things to a farmer:

      missing_step  an earlier job was never logged: a gap in the records, not
                    necessarily a mistake on the farm
      timing        the job was logged too early or too late
      order         jobs were logged in an impossible order
      repeat        the job was logged more often than the rules allow
      other         anything else, shown with the Knowledge Base's own wording
    """
    missing = MISSING_STEP.search(issue.message)
    if missing:
        needed = _name(missing.group(1))
        return "missing_step", f"The {needed} step has not been logged yet."
    if issue.rule_id in ("global-order", "engine-workflow-order"):
        return "order", "Logged in an order the crop's steps do not allow."
    timing = TIMING.search(issue.message)
    if timing:
        days, after = timing.group(1), _name(timing.group(2))
        minimum, maximum, low, high = timing.group(3), timing.group(4), timing.group(5), timing.group(6)
        if minimum:
            return "timing", f"Logged {days} days after {after}; at least {minimum} are expected."
        if maximum:
            return "timing", f"Logged {days} days after {after}; no more than {maximum} are expected."
        return "timing", f"Logged {days} days after {after}, inside a range the rules flag ({low} to {high} days)."
    if "entries within the last" in issue.message:
        return "repeat", "Logged more often than the rules allow."
    return "other", None


class KnowledgeBase(Protocol):
    def crop_steps(self, crop_id: str) -> ClientResult[CropSteps]: ...
    def validate(self, crop_id: str, history: list[dict], new_operation: dict, region: Optional[str] = None) -> ClientResult[ValidationReport]: ...


@dataclass(frozen=True)
class OperationVerdict:
    key: str
    status: str
    kb_operation_id: Optional[str] = None
    reason: Optional[str] = None
    issues: list[RuleIssue] = field(default_factory=list)


def check_cycle(
    crop_id: str,
    operations: list[LoggedOperation],
    kb: KnowledgeBase,
    region: Optional[str] = None,
    planted_on: Optional[date] = None,
) -> list[OperationVerdict]:
    """One verdict per operation, oldest first.

    planted_on is the crop cycle's own planting date, as AgTrail recorded it. When
    no planting operation was logged, it stands in for one, so a harvest is not
    called premature just because the planting was recorded on the cycle and not
    as an operation. It is used to judge the others and gets no verdict itself."""
    derived = None
    if planted_on is not None and not any(op.signal_category == "planting" for op in operations):
        derived = LoggedOperation(CYCLE_PLANTING_KEY, "planting", planted_on)
        operations = [*operations, derived]
    ordered = sorted(operations, key=lambda op: op.operation_date)

    steps_result = kb.crop_steps(crop_id)
    if not steps_result.ok:
        reason = f"Knowledge Base unavailable: {steps_result.unavailable_reason}"
        return [OperationVerdict(op.key, "unknown", reason=reason) for op in ordered if op is not derived]
    crop_steps = steps_result.value
    assignments = assign_steps(crop_steps, ordered)

    # Operations logged on the same day have no true order: the date has no clock
    # time, and the order they were saved in says nothing about the order they were
    # done in. Judging them in the order the crop's steps run (planting before
    # harvest) stops a same-day entry being flagged as "out of order" for no reason.
    step_number = {step.operation_id: step.step_number for step in crop_steps.steps}
    unranked = len(step_number) + 1

    def rank(op: LoggedOperation) -> int:
        return step_number.get(assignments[op.key].kb_operation_id, unranked)

    ordered = sorted(ordered, key=lambda op: (op.operation_date, rank(op)))

    category_of_step = {step.operation_id: step.signal_category for step in crop_steps.steps}
    logged_categories = {op.signal_category for op in ordered if op.signal_category}

    verdicts = []
    history: list[dict] = []
    for operation in ordered:
        assignment = assignments[operation.key]
        if assignment.kb_operation_id is None:
            verdicts.append(OperationVerdict(operation.key, assignment.status or "unknown", reason=assignment.reason))
            continue

        entry = {"operation_id": assignment.kb_operation_id, "date": operation.operation_date.isoformat()}
        if operation is derived:
            history.append(entry)  # only there to be built on, not judged
            continue
        result = kb.validate(crop_id, list(history), entry, region)
        history.append(entry)
        if not result.ok:
            verdicts.append(
                OperationVerdict(
                    operation.key, "unknown", assignment.kb_operation_id,
                    reason=f"Knowledge Base unavailable: {result.unavailable_reason}",
                )
            )
            continue
        verdicts.append(_verdict(operation.key, assignment, result.value, category_of_step, logged_categories))
    return [v for v in verdicts if v.key != CYCLE_PLANTING_KEY]


def _unique(issues: list[RuleIssue]) -> list[RuleIssue]:
    """Several rules can say the same thing in the same words; once is enough."""
    seen, result = set(), []
    for issue in issues:
        if issue.message not in seen:
            seen.add(issue.message)
            result.append(issue)
    return result


def _verdict(key, assignment: StepAssignment, report: ValidationReport, category_of_step, logged_categories) -> OperationVerdict:
    blocks, uncertain = [], []
    for issue in _unique(report.blocking_issues):
        missing = MISSING_STEP.search(issue.message)
        # "step X was never logged" is only a fact if nothing logged could have been X.
        # If operations of X's kind exist, one of them may be X and we could not tell.
        if missing and category_of_step.get(missing.group(1)) in logged_categories:
            uncertain.append(issue)
        else:
            blocks.append(issue)

    if blocks:
        return OperationVerdict(key, "blocked", assignment.kb_operation_id, issues=_unique(blocks + list(report.warnings)))
    if uncertain:
        return OperationVerdict(
            key, "unknown", assignment.kb_operation_id,
            reason="The rules need an earlier step that we could not identify among the operations logged.",
            issues=uncertain,
        )
    if report.warnings:
        return OperationVerdict(key, "warning", assignment.kb_operation_id, issues=_unique(list(report.warnings)))
    return OperationVerdict(key, "ok", assignment.kb_operation_id)
