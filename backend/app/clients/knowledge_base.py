"""
The hub's client for the Knowledge Base (validation.agrolinking.com).

It reads the shared reference data (the operation taxonomy, each crop's steps,
the crop-to-commodity map, the state-to-zone map and the data version) and asks
the Knowledge Base to judge an operation or say what comes next.

Every method returns a ClientResult, so a Knowledge Base outage shows up as an
"unavailable" section, never as an error page. A stale copy is served when the
service is down but we have an older answer.
"""

from functools import lru_cache
from typing import Optional

import httpx

from app.clients.base import ClientResult, ServiceClient
from app.clients.resilience import CircuitBreaker
from app.contracts.knowledge_base import (
    CommodityMapping,
    CropSteps,
    HarvestEstimateBasis,
    KnowledgeBaseVersion,
    RegionMapping,
    Suggestions,
    Taxonomy,
    ValidationReport,
)
from app.core.config import settings

# The reference files change only when the Knowledge Base is redeployed, so an
# hour matches the Cache-Control it sends. The version is checked more often,
# because it is how the hub learns that something changed.
REFERENCE_TTL_SECONDS = 3600
VERSION_TTL_SECONDS = 60


class KnowledgeBaseClient:
    def __init__(self, base_url: Optional[str] = None, *, transport: Optional[httpx.BaseTransport] = None, **options):
        self._service = ServiceClient(
            "Knowledge Base",
            base_url or settings.KNOWLEDGE_BASE_URL,
            transport=transport,
            **options,
        )

    @property
    def breaker(self) -> CircuitBreaker:
        return self._service.breaker

    def taxonomy(self) -> ClientResult[Taxonomy]:
        return self._service.fetch("/v1/taxonomy", Taxonomy, REFERENCE_TTL_SECONDS)

    def commodities(self) -> ClientResult[CommodityMapping]:
        return self._service.fetch("/v1/mappings/commodities", CommodityMapping, REFERENCE_TTL_SECONDS)

    def regions(self) -> ClientResult[RegionMapping]:
        return self._service.fetch("/v1/mappings/regions", RegionMapping, REFERENCE_TTL_SECONDS)

    def crop_steps(self, crop_id: str) -> ClientResult[CropSteps]:
        return self._service.fetch(f"/v1/crops/{crop_id}/steps", CropSteps, REFERENCE_TTL_SECONDS)

    def harvest_estimate(self, crop_id: str) -> ClientResult[HarvestEstimateBasis]:
        return self._service.fetch(f"/v1/crops/{crop_id}/harvest-estimate", HarvestEstimateBasis, REFERENCE_TTL_SECONDS)

    def validate(
        self,
        crop_id: str,
        history: list[dict],
        new_operation: dict,
        region: Optional[str] = None,
    ) -> ClientResult[ValidationReport]:
        """history and new_operation are {"operation_id": ..., "date": "YYYY-MM-DD"}."""
        body = {"crop_id": crop_id, "history": history, "new_operation": new_operation}
        if region:
            body["region"] = region
        return self._service.post("/v1/validate", body, ValidationReport)

    def suggestions(
        self,
        crop_id: str,
        history: list[dict],
        as_of: Optional[str] = None,
        region: Optional[str] = None,
    ) -> ClientResult[Suggestions]:
        body: dict = {"crop_id": crop_id, "history": history}
        if as_of:
            body["as_of"] = as_of
        if region:
            body["region"] = region
        return self._service.post("/v1/suggestions", body, Suggestions)

    def version(self) -> ClientResult[KnowledgeBaseVersion]:
        return self._service.fetch("/v1/version", KnowledgeBaseVersion, VERSION_TTL_SECONDS)

    def close(self) -> None:
        self._service.close()


@lru_cache(maxsize=1)
def get_knowledge_base_client() -> KnowledgeBaseClient:
    """One shared client per process, so the cache and the circuit breaker are shared too."""
    return KnowledgeBaseClient()
