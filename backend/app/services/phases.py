"""
Which phase a logged operation belongs to: pre-planting, planting, post-planting, harvest or
post-harvest. The phases and which job goes in which come from the Knowledge Base; this only
looks one up. The answer is the most specific one available, and None when there is none, never
a guess:

  1. the Knowledge Base step the operation was identified as, when it was;
  2. the operation's own logged name, when it is a job the Knowledge Base lists (irrigation,
     pruning), since those name their phase individually;
  3. the operation's job group, when that group has a phase.
"""

import re
from typing import Optional

from app.contracts.knowledge_base import Taxonomy


def _normal(logged_type: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (logged_type or "").strip().lower()).strip("_")


def phase_of(
    taxonomy: Optional[Taxonomy],
    *,
    signal_category: Optional[str] = None,
    kb_operation_id: Optional[str] = None,
    logged_type: Optional[str] = None,
    crop_id: Optional[str] = None,
) -> Optional[str]:
    if taxonomy is None:
        return None
    if kb_operation_id:
        phase = taxonomy.phase_for(crop_id, kb_operation_id)
        if phase:
            return phase
    named = _normal(logged_type)
    if named in taxonomy.operations:
        phase = taxonomy.phase_for(crop_id, named)
        if phase:
            return phase
    return taxonomy.phase_of_category(signal_category)
