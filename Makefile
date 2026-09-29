TEST_DATABASE_URL ?= postgresql://postgres:test@127.0.0.1:55432/postgres?sslmode=disable

.PHONY: install test test-db audit migrate run worker reprocess up

install:
	cd backend && .venv/bin/pip install torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cpu
	cd backend && .venv/bin/pip install -r requirements-dev.txt

# Starts a disposable PostGIS container for the database tests.
test-db:
	docker start agtrail_test_db 2>/dev/null || docker run -d --name agtrail_test_db -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=test postgis/postgis:16-3.4

test:
	cd backend && TEST_DATABASE_URL="$(TEST_DATABASE_URL)" .venv/bin/pytest -q

audit:
	cd backend && .venv/bin/pip-audit -r requirements.txt

migrate:
	cd backend && .venv/bin/alembic upgrade head

run:
	cd backend && .venv/bin/uvicorn app.main:app --reload

worker:
	cd backend && .venv/bin/python -m app.worker

reprocess:
	cd backend && .venv/bin/python -m app.jobs.reprocess_images

up:
	docker compose -f infra/docker/docker-compose.yml up --build
