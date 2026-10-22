"""
The shared list of what kind of farm operation something is. AgTrail, the
photo model and the Knowledge Base each name operations their own way, so
every logged operation is mapped onto this one list. See the integration
plan, section 6.1.
"""

import enum
import re
from datetime import timedelta, timezone

# Nigeria uses West Africa Time all year, no daylight saving, so a fixed
# offset is exact and needs no timezone database in the container.
LOCAL_TIMEZONE = timezone(timedelta(hours=1))


class SignalCategory(str, enum.Enum):
    land_preparation = "land_preparation"
    planting = "planting"
    weeding = "weeding"
    fertilizer_application = "fertilizer_application"
    pest_control = "pest_control"
    harvesting = "harvesting"
    sorting_grading = "sorting_grading"
    cleaning_drying = "cleaning_drying"
    processing = "processing"
    storage = "storage"
    other = "other"


def normalise_label(raw: str) -> str:
    """Lower-cases and collapses spaces, so "Soya  Beans " and "soya beans"
    look up the same alias."""
    return re.sub(r"\s+", " ", raw.strip().lower())
