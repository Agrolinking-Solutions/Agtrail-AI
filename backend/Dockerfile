FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# CPU-only torch is a fraction of the size of the default CUDA build.
RUN pip install torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install -r requirements.txt

RUN useradd --create-home --uid 10001 appuser
COPY --chown=appuser:appuser . .
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

# The web container brings the database schema up to date before it starts,
# so a deploy can never run new code against an old schema (the platform
# redeploys on every merge to main, and nothing else runs migrations). If the
# migration fails the container exits and the platform keeps the previous
# version running. Migrations must stay additive so the old version keeps
# working while the new one starts. Set RUN_MIGRATIONS=false to skip. The
# worker overrides this command, so it never runs migrations.
CMD ["sh", "-c", "if [ \"${RUN_MIGRATIONS:-true}\" != \"false\" ]; then alembic upgrade head; fi && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --workers 2"]
