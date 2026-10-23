"""
Judges every crop cycle's logged operations against the Knowledge Base's rules and
stores one verdict per operation in operation_signals.

    python -m app.jobs.judge_operations

Safe to run again, and meant to run often: each run rebuilds the verdicts from the
current history and the current rules, so a new operation or a Knowledge Base
change is picked up on the next run. Only cycles whose crop has a Knowledge Base
file are judged. A cycle is skipped, with its old verdicts kept, if the
Knowledge Base cannot be reached, so an outage never overwrites good verdicts
with "unknown".
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.clients.knowledge_base import get_knowledge_base_client
from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.models.crop_cycle import CropCycle
from app.models.farm_operation import FarmOperation
from app.models.operation_signal import OperationSignal
from app.services.operation_steps import LoggedOperation
from app.services.signal_engine import check_cycle

logger = logging.getLogger(__name__)


def judge_cycle(db: Session, cycle: CropCycle, kb, kb_version: str | None) -> str:
    """Returns "judged", or "skipped" when the Knowledge Base was unreachable."""
    operations = db.execute(
        select(FarmOperation).where(FarmOperation.crop_cycle_id == cycle.id).order_by(FarmOperation.created_at)
    ).scalars().all()
    if not operations:
        return "judged"

    by_key = {str(op.id): op for op in operations}
    logged = [LoggedOperation(str(op.id), op.signal_category, op.operation_date) for op in operations]
    verdicts = check_cycle(cycle.crop_id, logged, kb, planted_on=cycle.planted_date)

    if not _knowledge_base_answered(verdicts):
        return "skipped"

    for verdict in verdicts:
        operation = by_key[verdict.key]
        if verdict.kb_operation_id and operation.kb_operation_id != verdict.kb_operation_id:
            operation.kb_operation_id = verdict.kb_operation_id
        values = dict(
            operation_id=operation.id,
            farm_id=operation.farm_id,
            crop_cycle_id=cycle.id,
            status=verdict.status,
            kb_operation_id=verdict.kb_operation_id,
            reason=verdict.reason,
            issues=[issue.model_dump() for issue in verdict.issues],
            kb_version=kb_version,
        )
        stmt = insert(OperationSignal).values(**values)
        update = {k: v for k, v in values.items() if k != "operation_id"}
        db.execute(stmt.on_conflict_do_update(index_elements=["operation_id"], set_={**update, "checked_at": func.now()}))
    return "judged"


def _knowledge_base_answered(verdicts) -> bool:
    """False when every verdict is 'unknown' because the Knowledge Base was down."""
    down = [v for v in verdicts if v.status == "unknown" and (v.reason or "").startswith("Knowledge Base unavailable")]
    return not down


def judge_all(kb=None) -> dict:
    kb = kb or get_knowledge_base_client()
    version = kb.version()
    kb_version = version.value.kb_version if version.ok else None
    summary = {"cycles_judged": 0, "cycles_skipped": 0}

    with SessionLocal() as db:
        cycles = db.execute(select(CropCycle).where(CropCycle.crop_id.is_not(None))).scalars().all()
        for cycle in cycles:
            outcome = judge_cycle(db, cycle, kb, kb_version)
            summary["cycles_judged" if outcome == "judged" else "cycles_skipped"] += 1
        db.commit()
    return summary


def main() -> None:
    configure_logging()
    summary = judge_all()
    logger.info("Operation judging finished: %s", summary)


if __name__ == "__main__":
    main()
