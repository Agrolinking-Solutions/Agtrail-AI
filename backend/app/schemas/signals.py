import uuid
from datetime import date, datetime

from pydantic import BaseModel


class RuleIssueOut(BaseModel):
    rule_id: str
    severity: str
    message: str


class OperationSignalOut(BaseModel):
    operation_id: uuid.UUID
    operation_date: date
    logged_type: str
    # The Knowledge Base step this was identified as, when it could be told for certain.
    kb_operation_id: str | None
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
    signals: list[OperationSignalOut]
