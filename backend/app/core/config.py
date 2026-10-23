from pathlib import Path
from typing import List

from pydantic import model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from typing_extensions import Annotated

# Always read backend/.env, regardless of the directory the server or
# alembic is started from. Real environment variables still take priority.
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # App
    APP_NAME: str = "AgTrail AI"
    ENVIRONMENT: str = "development"
    ENABLE_DOCS: bool = False
    LOG_LEVEL: str = "INFO"
    SECRET_KEY: str
    ALLOWED_ORIGINS: Annotated[List[str], NoDecode] = ["http://localhost:3000"]

    # Comma separated "label:key" pairs, e.g. "dashboard:abc123,victor:def456".
    # The label is recorded as verified_by when a prediction is verified.
    API_KEYS: str = ""

    # Database. Either set DATABASE_URL to a full connection string, or set
    # the individual POSTGRES_* values and the URL is built from them.
    DATABASE_URL: str = ""
    POSTGRES_HOST: str = ""
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = ""
    POSTGRES_USER: str = ""
    POSTGRES_PASSWORD: str = ""
    DB_SSL_MODE: str = "require"

    # Cloud Storage
    GCP_PROJECT_ID: str = ""
    GCP_BUCKET_NAME: str = ""
    GOOGLE_APPLICATION_CREDENTIALS: str = ""

    # AgTrail Integration
    AGTRAIL_API_BASE_URL: str = "https://agtrail-backend-v2-production.up.railway.app"
    AGTRAIL_API_KEY: str = ""
    AGTRAIL_WEBHOOK_SECRET: str = ""
    # Secret for signing our outbound requests to AgTrail. Falls back to the
    # webhook secret until AgTrail issues a separate key for this direction.
    AGTRAIL_OUTBOUND_SECRET: str = ""

    # Knowledge Base: the crop rules and shared reference data. Read-only, no key.
    KNOWLEDGE_BASE_URL: str = "https://validation.agrolinking.com"

    # Copernicus Data Space (Sentinel-2 NDVI), OAuth client credentials
    CDSE_CLIENT_ID: str = ""
    CDSE_CLIENT_SECRET: str = ""

    # Mapbox, an alternate satellite imagery source alongside Copernicus
    MAPBOX_ACCESS_TOKEN: str = ""
    WEBHOOK_MAX_BODY_BYTES: int = 256 * 1024

    # Image fetching
    IMAGE_MAX_BYTES: int = 15 * 1024 * 1024
    IMAGE_MAX_PIXELS: int = 40_000_000
    IMAGE_ALLOW_HTTP: bool = False
    CONTACT_EMAIL: str = ""

    # ML
    MODEL_PATH: str = str(BACKEND_DIR / "ml" / "models")
    MIN_PREDICTION_CONFIDENCE: float = 0.5
    MLFLOW_TRACKING_URI: str = "http://localhost:5000"

    @model_validator(mode="before")
    @classmethod
    def _split_origins(cls, values):
        origins = values.get("ALLOWED_ORIGINS")
        if isinstance(origins, str):
            values["ALLOWED_ORIGINS"] = [o.strip() for o in origins.split(",") if o.strip()]
        return values

    @model_validator(mode="after")
    def _check_required(self):
        if not self.DATABASE_URL and not (self.POSTGRES_HOST and self.POSTGRES_DB and self.POSTGRES_USER):
            raise ValueError("Set DATABASE_URL, or POSTGRES_HOST, POSTGRES_DB and POSTGRES_USER.")
        if "://" in self.POSTGRES_HOST:
            raise ValueError("POSTGRES_HOST contains a full connection string, put it in DATABASE_URL instead.")

        if self.is_production:
            problems = []
            if len(self.SECRET_KEY) < 32 or "changeme" in self.SECRET_KEY:
                problems.append("SECRET_KEY must be a random value of at least 32 characters")
            if not self.AGTRAIL_WEBHOOK_SECRET:
                problems.append("AGTRAIL_WEBHOOK_SECRET is required")
            if not self.api_keys:
                problems.append("API_KEYS is required")
            if self.DB_SSL_MODE not in ("require", "verify-ca", "verify-full"):
                problems.append("DB_SSL_MODE must be require, verify-ca or verify-full")
            if self.IMAGE_ALLOW_HTTP:
                problems.append("IMAGE_ALLOW_HTTP must be false")
            if problems:
                raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() in ("production", "prod")

    @property
    def api_keys(self) -> dict[str, str]:
        """Maps each API key to its label."""
        keys = {}
        for entry in self.API_KEYS.split(","):
            label, sep, key = entry.strip().partition(":")
            if sep and label and key:
                keys[key] = label
        return keys

    @property
    def outbound_secret(self) -> str:
        return self.AGTRAIL_OUTBOUND_SECRET or self.AGTRAIL_WEBHOOK_SECRET

    @property
    def database_url(self) -> URL:
        if self.DATABASE_URL:
            url = make_url(self.DATABASE_URL.replace("postgres://", "postgresql://", 1))
            if url.drivername == "postgresql":
                url = url.set(drivername="postgresql+psycopg2")
            if "sslmode" not in url.query:
                url = url.update_query_dict({"sslmode": self.DB_SSL_MODE})
            return url

        # URL.create escapes credentials, so passwords containing @ : / etc
        # don't break the connection string.
        return URL.create(
            "postgresql+psycopg2",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
            query={"sslmode": self.DB_SSL_MODE},
        )

    @property
    def user_agent(self) -> str:
        contact = f" (contact: {self.CONTACT_EMAIL})" if self.CONTACT_EMAIL else ""
        return f"AgtrailInferenceService/1.0{contact}"


settings = Settings()
