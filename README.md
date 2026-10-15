# AgTrail AI

The AI and satellite intelligence layer behind AgTrail. It receives signed
webhooks from AgTrail (new farms, crop cycles, monitoring photos),
classifies each photo's farming stage with an image model, and serves the
predictions back to AgTrail. It also serves each farm's location,
vegetation trend, and satellite imagery, for the dashboard's map view.
AgTrail owns the frontend and the farmer/agent data; this repo is the
backend engine that plugs into it, not a standalone product.

| Folder | Contents |
| --- | --- |
| `backend/` | FastAPI service, database models, Alembic migrations, ML inference, job queue |
| `backend/ml/` | Training script, dataset download tools, exported model |
| `ml/datasets/` | Training images (not in git) and their license log |
| `infra/docker/` | Local and production Docker Compose files |

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
| `GET /farms/{farm_id}/location` | `X-API-Key` |
| `GET /farms/{farm_id}/ndvi?limit=` | `X-API-Key` |
| `GET /farms/{farm_id}/ndvi-image?style=` | `X-API-Key` |
| `GET /health`, `GET /health/ready` | none |

The `farms/` endpoints back the dashboard's map and satellite view, not the
prediction pipeline above. `location` gives a point to fly/pan to and the
mapped boundary to draw. `ndvi` is the vegetation trend chart, from
Sentinel-2 via Copernicus Data Space. `ndvi-image` returns a `image/png`
directly (not JSON) and takes a `style` of `ndvi`, `true-color`, `mapbox`,
or `mapbox-area`; the first two come from the same Sentinel-2 observation
as the trend chart, the Mapbox styles are a separate, higher-resolution
provider with no time window — `mapbox` tightly framed on the farm itself,
`mapbox-area` a wider fixed-zoom view for surrounding context. Full
request/response shapes are kept in a shared API reference doc, not here —
ask in the team channel for the link. `ndvi` and `ndvi-image` need
`CDSE_CLIENT_ID`/`CDSE_CLIENT_SECRET` set; the Mapbox styles need
`MAPBOX_ACCESS_TOKEN`.

## Tests

```bash
make test-db   # disposable PostGIS container on 127.0.0.1:55432
make test
make audit     # known vulnerabilities in pinned dependencies
```

CI runs the tests, `pip-audit` and a gitleaks secret scan on every push.

## Background work

Photo classification runs as a job on a Postgres-backed queue (`jobs`
table), not inline in the webhook request. The webhook handler enqueues a
`run_inference` job in the same transaction as the image row, then a
worker claims and runs jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, so
scaling up is just running more workers, no broker required.

The worker runs in one of two modes:

```bash
make worker        # long-running: polls forever (python -m app.worker)
make worker-once   # cron mode: drains the queue, then exits (--once)
make reprocess     # repair tool: catch a pending image with no job at all
```

In production this runs as a **scheduled job** (Render Cron Job, or any
host's cron) calling `python -m app.worker --once` every few minutes,
rather than an always-on process — cheaper at low volume, at the cost of
predictions lagging by up to the schedule's interval instead of landing
within seconds. Switch to the long-running `python -m app.worker` (no
`--once`) if volume later justifies the always-on cost; both modes read
the same queue and coordinate safely with each other.

A failed job retries with exponential backoff up to `max_attempts`, then
is marked `dead` rather than retried forever; `last_error` on the row
says why. `--once` also requeues stale (crashed-worker) jobs on every
run, so nothing further is needed for that; `python -m app.jobs.reprocess_images`
only remains as an edge-case safety net for a pending image that
somehow has no job at all.

## Farm boundaries

`farms.boundary` isn't populated by the webhook (AgTrail's boundary can
be redrawn after a farm is created, with no `farm.updated` event to
tell us). Nothing keeps it in sync unless this runs:

```bash
make sync-boundaries   # pulls every mapped farm's polygon from AgTrail, batches of 100
```

Without this job, `boundary_mapped` stays false forever and the
GPS-verification check on incoming photos has nothing to check against.
Schedule it monthly (matching AgTrail's own suggested cadence, and how
often boundaries actually change) — same Render Cron Job pattern as the
worker, `python -m app.jobs.sync_boundaries` as the command.

## Training

```bash
cd backend && .venv/bin/pip install -r requirements-ml.txt
python ml/utils/dataset_report.py
python ml/training/train_stage_classifier.py --epochs 10
```
