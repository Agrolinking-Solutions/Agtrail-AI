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
| `POST /check` | `X-API-Key` |
| `POST /check/image?style=` | `X-API-Key` |
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

`{farm_id}` on any of the above accepts either our own `id` or AgTrail's
own farm id (`agtrail_farm_id`) — a caller only holding AgTrail's id
doesn't need a separate lookup to resolve it to ours first.

`check` and `check/image` are the same engine with no AgTrail farm
behind it: the caller supplies a GeoJSON boundary directly in the
request body (`{"boundary": {...}}`) instead of a `farm_id`, for a plot
that's never been registered with AgTrail at all. This is what the
AgLens monetization product (a separate paid API/frontend, billed and
orchestrated entirely outside this repo) calls. The boundary is
validated (a real, sane-sized polygon) via PostGIS before any
Copernicus or Mapbox call runs against it; neither endpoint is cached,
since each call is a billable action against a caller-supplied
geometry rather than a repeat view of a farm already on file.

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

## Satellite readings

```bash
python -m app.jobs.ingest_ndvi   # every mapped farm: 10-day NDVI, plus one row per satellite pass
```

Schedule it **daily** (Sentinel-2 passes about every 5 days). It is safe to
re-run: rows are updated, never duplicated. The per-pass rows go in
`farm_ndvi_scenes`, with clouds removed pixel by pixel, and are served by
`GET /farms/{id}/crop-health` (latest usable reading and its age in days).
A farm with no boundary is skipped, so `sync_boundaries` above has to run
first for newly mapped farms to appear.

## Image archive

Each monitoring photo is copied from AgTrail's own link into our storage, so it
outlives AgTrail's links and a prediction can always be re-checked. Files are
named by their content hash, so the same photo is stored once.

Storage is our private S3-compatible bucket on the VPS (Garage). Set these five
in Coolify (Environment Variables, **Buildtime off**) and redeploy; the secret
never goes in git, chat or tickets:

```
S3_ENDPOINT  S3_REGION  S3_BUCKET  S3_ACCESS_KEY  S3_SECRET_KEY
```

If `S3_BUCKET` is empty and `GCP_BUCKET_NAME` is set, Google Cloud Storage is used
instead. If both are empty, archiving is off and no archive jobs are queued.
`farm_images.archived_url` holds a pointer (`s3://bucket/key`), not a link: files
are private, so a link that works for a limited time is made from the pointer when
one is needed (`generate_presigned_url`, up to 7 days).

Photos that arrived before archiving was on are queued once with
`python -m app.jobs.backfill_archive` (safe to run again); the worker then copies them.

Limits: 100 MB per request (Cloudflare). **No backups yet**: the bucket is a single
server disk, so it is not the only copy of anything critical until backups exist.

## Rule checks on logged operations

```bash
python -m app.jobs.judge_operations   # judge every crop cycle's operations against the Knowledge Base
```

Schedule it **hourly**. Each run rebuilds one verdict per operation in
`operation_signals`, so new operations and Knowledge Base changes are picked up
on the next run. If the Knowledge Base cannot be reached, the old verdicts are
kept. Served by `GET /farms/{id}/signals`: `ok`, `warning`, `flagged` (a rule is
broken), `not_applicable` or `unknown` (not judged, with the reason). An operation
is only judged when it can be identified for certain.

Every signal also carries its **phase** (pre-planting, planting, post-planting, harvest or
post-harvest), looked up from the Knowledge Base when the endpoint is called: from the step the
operation was identified as, else from its own logged name (irrigation, pruning), else from its
job group. An operation with no phase says so (`phase: null`, counted under `none`), and if the
Knowledge Base cannot be reached the signals still come back with `phases_available: false`.
`/farms/{id}/intelligence` has the same operations grouped by phase in `signals.by_phase`.

The daily `ingest_ndvi` run looks back 20 days for farms that already have
satellite passes, and 90 days for a farm that has none yet.

## Trash

A farm that should not stay in the live tables (a test record, a duplicate) is moved to the
trash, not deleted. Everything attached to it (crop cycles, operations, satellite readings,
rule checks, visits, photos, predictions) is saved whole in `trashed_records`, so it can be
shown to the team or put back.

```bash
python -m app.jobs.trash list
python -m app.jobs.trash farm TEST-FARM-001                       # preview only, nothing moves
python -m app.jobs.trash farm TEST-FARM-001 --yes --reason "test record"
python -m app.jobs.trash export <trash id>                        # prints the whole record as JSON
python -m app.jobs.trash restore <trash id> --yes                 # puts every row back
```

Nothing happens without `--yes`. The farmer is removed only when it was their only farm.
The trash lives in the database, not in a folder, because a container's own files are lost
at every deploy.

## Deploys and migrations

The production platform redeploys on every merge to `main`. The web container
runs `alembic upgrade head` before it starts, so the database schema is always
brought up to date by the deploy itself. If a migration fails, the container
exits and the platform keeps the previous version running.

Keep migrations **additive** (add tables and columns, do not rename or drop
anything in the same release), so the previous version keeps working while the
new one starts. Set `RUN_MIGRATIONS=false` to skip the step. The worker
(`app.worker --once`) never runs migrations.

If the platform has a custom start command set, it replaces the one in the
`Dockerfile` and migrations will not run. Leave it empty.

## Training

```bash
cd backend && .venv/bin/pip install -r requirements-ml.txt
python ml/utils/dataset_report.py
python ml/training/train_stage_classifier.py --epochs 10
```
