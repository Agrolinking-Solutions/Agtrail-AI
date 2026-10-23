"""
The hub's side of the contract with PIS, the Agrolinking commodity price and
weather service (pis-api.agrolinking.com).

Same rules as the Knowledge Base contract: these models say what the hub needs,
fields PIS adds later are ignored, and a missing or mistyped field fails the
response as a contract error instead of passing bad data on. JSON Schema copies
live in backend/contracts/pis/ and a test keeps them in step.

One thing to know about PIS prices. The price it reports "today" is often the last
real price carried forward: latest_real_price says when and where a price was
really seen, and start_is_estimate says whether today's figure is only that
carried-forward number. The /commodities list shows the date of the last pipeline
run for every commodity, not when a price was observed, so it must not be used to
say how fresh a price is.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class _Contract(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PisHealth(_Contract):
    status: str
    timestamp: Optional[str] = None


class LatestRealPrice(_Contract):
    price_per_unit: float
    date: str
    source: str
    kind: Optional[str] = None


class Horizon(_Contract):
    forecast_date: str
    forecast_price: float
    pct_change: float
    direction: str


class CommodityForecast(_Contract):
    """The price now, how real it is, and the forecast at each horizon."""

    commodity: str
    run_date: str
    unit_label: str
    last_known_date: Optional[str] = None
    latest_real_price: Optional[LatestRealPrice] = None
    start_is_estimate: bool = False
    today_price_per_unit: float
    today_date: str
    currency: str = "NGN"
    horizons: dict[str, Horizon] = Field(default_factory=dict)


class OutlookEntry(_Contract):
    pct_change_30d: float
    direction: str
    signal: str
    capped: bool = False


class Outlook30d(_Contract):
    """Expected 30-day price change for each commodity."""

    as_of: str
    avg_pct_change: float
    direction: str
    signal: str
    interpretation: str
    per_commodity: dict[str, OutlookEntry]


class WeatherDay(_Contract):
    date: str
    day: str
    condition: str
    symbol: Optional[str] = None
    high_c: float
    low_c: float
    rain_mm: float
    wind_max_kmh: Optional[float] = None
    humidity_pct: Optional[float] = None


class WeatherPlace(_Contract):
    state: str
    city: str
    zone: Optional[str] = None
    zone_main_city: bool = False
    days: list[WeatherDay]


class WeatherToday(_Contract):
    """A 7-day forecast for one city in each state PIS covers."""

    as_of: str
    issued_at: Optional[str] = None
    source: str
    attribution: str
    license: Optional[str] = None
    places: list[WeatherPlace]

    def for_state(self, state: str) -> Optional[WeatherPlace]:
        wanted = state.strip().lower()
        return next((place for place in self.places if place.state.lower() == wanted), None)


class SeasonMonth(_Contract):
    score: float
    is_current: bool = False
    interpretation: str


class Seasonality(_Contract):
    """Where in the price season the current month sits (100 peak harvest, 0 lean)."""

    commodity: str
    as_of: str
    current_month: str
    current_score: float
    note: str
    data_source: Optional[str] = None
    monthly_profile: dict[str, SeasonMonth]


PIS_CONTRACT_MODELS = {
    "health": PisHealth,
    "commodity_forecast": CommodityForecast,
    "outlook_30d": Outlook30d,
    "weather_today": WeatherToday,
    "seasonality": Seasonality,
}
