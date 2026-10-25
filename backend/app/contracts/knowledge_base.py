"""
The hub's side of the contract with the Knowledge Base's /v1 reference data.

These models describe what the hub needs and expects. Extra fields the
Knowledge Base adds later are ignored on purpose (additive changes must never
break the hub), but a missing or mistyped field makes the response fail to
parse, which the client treats as a contract error rather than passing bad data
on. JSON Schema copies live in backend/contracts/knowledge_base/ and a test
keeps them in step with these models.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Contract(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Phase(_Contract):
    """One of the five phases a job falls into (pre-planting, planting, post-planting, harvest, post-harvest)."""

    id: str
    name: str
    description: str = ""


class Category(_Contract):
    id: str
    description: str
    satellite_evidence: str
    # The phase this group of jobs belongs to. None for the catch-all group, whose jobs name their own.
    phase: Optional[str] = None


class OperationMapping(_Contract):
    name: str
    kb_category: str
    signal_category: str
    review_status: str
    reason: str
    # Set on a job whose phase differs from its group's, such as irrigation inside "something else".
    phase: Optional[str] = None


class Taxonomy(_Contract):
    """The shared operation categories and the operation_id mapping."""

    review_status: str
    categories: list[Category]
    operations: dict[str, OperationMapping]
    crop_overrides: dict[str, dict[str, str]] = Field(default_factory=dict)
    phases: list[Phase] = Field(default_factory=list)

    def signal_category_for(self, crop_id: Optional[str], operation_id: str) -> Optional[str]:
        """The shared category for an operation, or None if it has no mapping yet."""
        if crop_id:
            override = self.crop_overrides.get(crop_id, {}).get(operation_id)
            if override:
                return override
        entry = self.operations.get(operation_id)
        return entry.signal_category if entry else None


    def phase_name(self, phase_id: Optional[str]) -> Optional[str]:
        return next((p.name for p in self.phases if p.id == phase_id), None)

    def phase_of_category(self, category_id: Optional[str]) -> Optional[str]:
        return next((c.phase for c in self.categories if c.id == category_id), None)

    def phase_for(self, crop_id: Optional[str], operation_id: str) -> Optional[str]:
        """The phase of a Knowledge Base job: its own if it names one, otherwise its group's."""
        entry = self.operations.get(operation_id)
        if entry is None:
            return None
        return entry.phase or self.phase_of_category(self.signal_category_for(crop_id, operation_id))


class CommodityRow(_Contract):
    crop_id: str
    pis_commodity: Optional[str]
    conversion_factor: Optional[float]
    conversion_note: str
    price_status: Literal["priced", "unpriced"]
    review_status: str


class CommodityMapping(_Contract):
    """Which PIS commodity prices each crop, and the yield conversion factor."""

    crops: list[CommodityRow]
    pis_commodities_without_a_crop_file: dict[str, str] = Field(default_factory=dict)

    def for_crop(self, crop_id: str) -> Optional[CommodityRow]:
        return next((row for row in self.crops if row.crop_id == crop_id), None)


class StateRow(_Contract):
    state: str
    geo_zone: str
    agro_zone: Optional[str]
    agro_zone_crosses_borders: bool
    has_pis_state_prices: bool
    agro_zone_review_status: str


class RegionMapping(_Contract):
    """Each state's geopolitical zone and default agro-ecological zone."""

    states: list[StateRow]

    def for_state(self, state: str) -> Optional[StateRow]:
        wanted = state.strip().lower()
        return next((row for row in self.states if row.state.lower() == wanted), None)


class KnowledgeBaseVersion(_Contract):
    """kb_version changes whenever any Knowledge Base data changes."""

    kb_version: str
    commit: Optional[str] = None


class CropStep(_Contract):
    step_number: int
    operation_id: str
    name: str
    signal_category: Optional[str] = None
    required: bool = False


class CropSteps(_Contract):
    """A crop's steps in workflow order, each with its shared signal category."""

    crop_id: str
    steps: list[CropStep]

    def steps_in_category(self, signal_category: str) -> list[CropStep]:
        return [step for step in self.steps if step.signal_category == signal_category]


class RuleIssue(_Contract):
    rule_id: str
    severity: str
    message: str


class ValidationReport(_Contract):
    """The Knowledge Base's verdict on one operation, given the history before it."""

    crop_id: str
    operation_id: str
    date: str
    blocked: bool
    issues: list[RuleIssue] = Field(default_factory=list)
    warnings: list[RuleIssue] = Field(default_factory=list)
    blocking_issues: list[RuleIssue] = Field(default_factory=list)


class NextStep(_Contract):
    operation_id: str
    status: str
    message: str
    depends_on_operation_id: Optional[str] = None
    depends_on_date: Optional[str] = None
    earliest_date: Optional[str] = None
    latest_date: Optional[str] = None
    days_until_due: Optional[int] = None
    days_overdue: Optional[int] = None


class Suggestions(_Contract):
    """What the crop's rules expect to be logged next."""

    crop_id: str
    as_of: str
    cycle_status: str
    suggestion: Optional[NextStep] = None


# Used by the schema export and its test.
CONTRACT_MODELS = {
    "crop_steps": CropSteps,
    "validation": ValidationReport,
    "suggestions": Suggestions,
    "taxonomy": Taxonomy,
    "commodities": CommodityMapping,
    "regions": RegionMapping,
    "version": KnowledgeBaseVersion,
}
