from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import agtrail_webhook
from app.api.routes import predictions
from app.core.config import settings

app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    description="AI-powered farm monitoring and traceability system",
    docs_url="/docs" if settings.ENABLE_DOCS else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(predictions.router)
app.include_router(agtrail_webhook.router)

@app.get("/health")
async def health_check():
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.ENVIRONMENT}
