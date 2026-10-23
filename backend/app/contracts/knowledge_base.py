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


class Category(_Contract):
    id: str
    description: str
    satellite_evidence: str


class OperationMapping(_Contract):
    name: str
    kb_category: str
    signal_category: str
    review_status: str
    reason: str


class Taxonomy(_Contract):
    """The shared operation categories and the operation_id mapping."""

    review_status: str
    categories: list[Category]
    operations: dict[str, OperationMapping]
    crop_overrides: dict[str, dict[str, str]] = Field(default_factory=dict)

    def signal_category_for(self, crop_id: Optional[str], operation_id: str) -> Optional[str]:
        """The shared category for an operation, or None if it has no mapping yet."""
        if crop_id:
            override = self.crop_overrides.get(crop_id, {}).get(operation_id)
            if override:
                return override
        entry = self.operations.get(operation_id)
        return entry.signal_category if entry else None


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


# Used by the schema export and its test.
CONTRACT_MODELS = {
    "taxonomy": Taxonomy,
    "commodities": CommodityMapping,
    "regions": RegionMapping,
    "version": KnowledgeBaseVersion,
}
