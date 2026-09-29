from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.routes import agtrail_webhook
from app.api.routes import predictions
from app.core.config import settings
from app.core.database import get_db
from app.core.logging import configure_logging

configure_logging()

app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    description="AI-powered farm monitoring and traceability system",
    docs_url="/docs" if settings.ENABLE_DOCS else None,
    redoc_url="/redoc" if settings.ENABLE_DOCS else None,
    openapi_url="/openapi.json" if settings.ENABLE_DOCS else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if settings.is_production:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.include_router(predictions.router)
app.include_router(agtrail_webhook.router)


@app.get("/health")
async def health_check():
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/health/ready")
def readiness_check(db: Session = Depends(get_db)):
    """Readiness: the process can reach the database."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ok"}
