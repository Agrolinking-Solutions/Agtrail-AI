# AgTrail AI

Farm monitoring and traceability service for AgTrail. It receives signed
webhooks from AgTrail (new farms, crop cycles, monitoring photos),
classifies each photo's farming stage with an image model, and serves
the predictions to authorised callers.

| Folder | Contents |
| --- | --- |
| `backend/` | FastAPI service, database models, Alembic migrations, ML inference |
| `backend/ml/` | Training script, dataset download tools, exported model |
| `ml/datasets/` | Training images (not in git) and their license log |
| `infra/docker/` | Local and production Docker Compose files |
| `dashboard/`, `mobile/` | Placeholders, not built yet |

## Setup

```bash
cd backend
python3.12 -m venv .venv
cd .. && make install
cp backend/.env.example backend/.env   # then fill it in
make migrate
make run                                # http://127.0.0.1:8000
```

The backend always reads `backend/.env`. With `ENVIRONMENT=production` it
refuses to start unless `SECRET_KEY`, `AGTRAIL_WEBHOOK_SECRET` and
`API_KEYS` are set and the database connection uses TLS.

## API

| Endpoint | Auth |
| --- | --- |
| `POST /webhooks/agtrail/farm-event` | HMAC-SHA256 of the raw body in `X-AgTrail-Signature` |
| `GET /predictions/{farm_id}` | `X-API-Key` |
| `GET /predictions/{farm_id}/history?limit=&offset=` | `X-API-Key` |
| `POST /predictions/{prediction_id}/verify` | `X-API-Key`, recorded as `verified_by` |
| `GET /health`, `GET /health/ready` | none |

## Tests

```bash
make test-db   # disposable PostGIS container on 127.0.0.1:55432
make test
make audit     # known vulnerabilities in pinned dependencies
```

CI runs the tests, `pip-audit` and a gitleaks secret scan on every push.

## Background work

Photo classification runs after the webhook responds. Images left
`pending` (e.g. after a restart) are retried by
`python -m app.jobs.reprocess_images`; schedule it every 15 minutes.

## Training

```bash
cd backend && .venv/bin/pip install -r requirements-ml.txt
python ml/utils/dataset_report.py
python ml/training/train_stage_classifier.py --epochs 10
```
