"""
The hub's client for PIS (pis-api.agrolinking.com): prices, the 30-day outlook,
seasonality and weather. Public, read-only, no key.

Every method returns a ClientResult, so a PIS outage shows up as "unavailable",
never as an error page, and the last good copy is served when the service is down
but we have one.
"""

from functools import lru_cache
from typing import Optional
from urllib.parse import quote

import httpx

from app.clients.base import ClientResult, ServiceClient
from app.clients.resilience import CircuitBreaker
from app.contracts.pis import CommodityForecast, Outlook30d, PisHealth, Seasonality, WeatherToday
from app.core.config import settings

# PIS recomputes prices and the outlook daily and issues weather a few times a day.
PRICE_TTL_SECONDS = 3600
WEATHER_TTL_SECONDS = 1800
HEALTH_TTL_SECONDS = 60


class PisClient:
    def __init__(self, base_url: Optional[str] = None, *, transport: Optional[httpx.BaseTransport] = None, **options):
        self._service = ServiceClient(
            "PIS",
            base_url or settings.PIS_API_URL,
            transport=transport,
            **options,
        )

    @property
    def breaker(self) -> CircuitBreaker:
        return self._service.breaker

    def health(self) -> ClientResult[PisHealth]:
        return self._service.fetch("/health", PisHealth, HEALTH_TTL_SECONDS)

    def forecast(self, commodity: str) -> ClientResult[CommodityForecast]:
        """commodity is PIS's own name, such as "Maize (white)" (the Knowledge Base maps crops to these)."""
        return self._service.fetch(f"/forecasts/{quote(commodity, safe='')}", CommodityForecast, PRICE_TTL_SECONDS)

    def outlook(self) -> ClientResult[Outlook30d]:
        return self._service.fetch("/outlook/30d", Outlook30d, PRICE_TTL_SECONDS)

    def seasonality(self, commodity: str) -> ClientResult[Seasonality]:
        return self._service.fetch(f"/seasonality/{quote(commodity, safe='')}", Seasonality, PRICE_TTL_SECONDS)

    def weather(self) -> ClientResult[WeatherToday]:
        return self._service.fetch("/weather/today", WeatherToday, WEATHER_TTL_SECONDS)

    def close(self) -> None:
        self._service.close()


@lru_cache(maxsize=1)
def get_pis_client() -> PisClient:
    """One shared client per process, so the cache and the circuit breaker are shared too."""
    return PisClient()
