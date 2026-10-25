import uuid
from datetime import date, datetime

from pydantic import BaseModel


class RuleIssueOut(BaseModel):
    rule_id: str
    severity: str
    # The Knowledge Base's own wording, unchanged.
    message: str
    # missing_step | timing | order | repeat | other. A missing_step is a gap in the
    # records; the others are about what was done.
    kind: str
    # A short plain-language line, when the message follows a known pattern.
    summary: str | None = None


class OperationSignalOut(BaseModel):
    operation_id: uuid.UUID
    crop_cycle_id: uuid.UUID
    # The Knowledge Base crop the rules were taken from, and the crop as typed in AgTrail.
    crop_id: str | None
    crop: str
    operation_date: date
    logged_type: str
    # The Knowledge Base step this was identified as, when it could be told for certain.
    kb_operation_id: str | None
    # pre_planting | planting | post_planting | harvest | post_harvest, or None when it has no phase.
    phase: str | None = None
    phase_name: str | None = None
    # ok | warning | flagged | not_applicable | unknown
    status: str
    reason: str | None
    issues: list[RuleIssueOut]
    checked_at: datetime


class FarmSignalsOut(BaseModel):
    farm_id: uuid.UUID
    # A rule check on logged data: derived, not seen directly.
    provenance: str = "inferred"
    kb_version: str | None
    counts: dict[str, int]
    # False when the Knowledge Base could not be reached, so no phase could be looked up.
    phases_available: bool = True
    # How many operations are in each phase ("none" for those with no phase).
    phase_counts: dict[str, int] = {}
    # How many issues of each kind, across all signals.
    issue_kinds: dict[str, int]
    signals: list[OperationSignalOut]
