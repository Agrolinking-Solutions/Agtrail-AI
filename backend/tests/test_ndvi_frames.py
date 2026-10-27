"""Keeping a picture of each clear satellite pass, for the farm's timelapse. Copernicus and the bucket are
replaced by in-memory fakes; the database is real."""

from datetime import date

import pytest
from sqlalchemy import select

from tests.conftest import API_KEY, requires_db

pytestmark = requires_db

PNG = b"\x89PNG\r\n\x1a\nfake-frame"


class FakeBucket:
    def __init__(self):
        self.objects = {}
        self.renders = []
        self.fail_on = set()


@pytest.fixture
def bucket(monkeypatch):
    from app.services import ndvi_frames

    fake = FakeBucket()
    monkeypatch.setattr(ndvi_frames.settings, "S3_BUCKET", "aglens-images")

    def render(boundary, scene_date, token):
        fake.renders.append(scene_date)
        if scene_date in fake.fail_on:
            raise RuntimeError("Copernicus says no")
        return PNG + scene_date.isoformat().encode()

    def store(data, key):
        fake.objects[key] = data
        return f"s3://aglens-images/{key}"

    monkeypatch.setattr(ndvi_frames, "render_png", render)
    monkeypatch.setattr(ndvi_frames, "store_png", store)
    monkeypatch.setattr(ndvi_frames, "read_png", lambda pointer: fake.objects[pointer.removeprefix("s3://aglens-images/")])
    import app.api.routes.farms as farms_routes
    monkeypatch.setattr(farms_routes, "read_png", ndvi_frames.read_png)
    return fake


@pytest.fixture
def farm_with_scenes(db_session):
    from app.models import Farm, FarmNdviScene, Farmer

    farmer = Farmer(full_name="T", phone="1", state="Oyo", lga="x", agtrail_farmer_id="t-frames")
    db_session.add(farmer); db_session.flush()
    farm = Farm(farmer_id=farmer.id, name="f", agtrail_farm_id="t-frames-farm")
    db_session.add(farm); db_session.flush()
    for d, ndvi, clear in [(date(2026, 10, 1), 0.41, 1.0), (date(2026, 10, 6), None, 0.0), (date(2026, 10, 11), 0.44, 0.45),
                           (date(2026, 10, 16), 0.47, 0.6), (date(2026, 10, 21), 0.52, 1.0)]:
        db_session.add(FarmNdviScene(farm_id=farm.id, scene_date=d, ndvi_mean=ndvi, polygon_pixels=40, clear_fraction=clear))
    db_session.commit()
    return farm


def test_only_passes_with_at_least_half_the_farm_clear_get_a_frame(db_session, farm_with_scenes, bucket):
    from app.models import FarmNdviFrame
    from app.services.ndvi_frames import save_missing_frames

    result = save_missing_frames(db_session, farm_with_scenes, {"type": "Polygon"}, "token")

    dates = sorted(f.scene_date for f in db_session.execute(select(FarmNdviFrame)).scalars())
    assert result == {"saved": 3, "failed": 0}
    assert dates == [date(2026, 10, 1), date(2026, 10, 16), date(2026, 10, 21)]


def test_a_frame_is_made_once_and_a_second_run_does_nothing(db_session, farm_with_scenes, bucket):
    from app.services.ndvi_frames import save_missing_frames

    save_missing_frames(db_session, farm_with_scenes, {"type": "Polygon"}, "token")
    renders_after_first = len(bucket.renders)
    again = save_missing_frames(db_session, farm_with_scenes, {"type": "Polygon"}, "token")

    assert again == {"saved": 0, "failed": 0} and len(bucket.renders) == renders_after_first


def test_a_failed_frame_is_skipped_and_the_others_still_save(db_session, farm_with_scenes, bucket):
    from app.services.ndvi_frames import save_missing_frames

    bucket.fail_on.add(date(2026, 10, 16))
    result = save_missing_frames(db_session, farm_with_scenes, {"type": "Polygon"}, "token")
    assert result == {"saved": 2, "failed": 1}

    bucket.fail_on.clear()
    assert save_missing_frames(db_session, farm_with_scenes, {"type": "Polygon"}, "token") == {"saved": 1, "failed": 0}


def test_a_run_saves_at_most_the_per_run_limit_newest_first(db_session, farm_with_scenes, bucket):
    from app.models import FarmNdviFrame
    from app.services.ndvi_frames import save_missing_frames

    save_missing_frames(db_session, farm_with_scenes, {"type": "Polygon"}, "token", limit=1)

    assert [f.scene_date for f in db_session.execute(select(FarmNdviFrame)).scalars()] == [date(2026, 10, 21)]


def test_nothing_is_made_when_no_bucket_is_configured(db_session, farm_with_scenes, monkeypatch):
    from app.services import ndvi_frames

    monkeypatch.setattr(ndvi_frames.settings, "S3_BUCKET", "")
    assert ndvi_frames.save_missing_frames(db_session, farm_with_scenes, {"type": "Polygon"}, "token") == {"saved": 0, "failed": 0}


def test_the_list_is_oldest_first_and_each_frame_can_be_fetched(client, db_session, farm_with_scenes, bucket):
    from app.services.ndvi_frames import save_missing_frames

    save_missing_frames(db_session, farm_with_scenes, {"type": "Polygon"}, "token")
    headers = {"X-API-Key": API_KEY}

    listing = client.get(f"/farms/{farm_with_scenes.id}/ndvi-frames", headers=headers)
    body = listing.json()
    assert listing.status_code == 200 and body["count"] == 3 and body["provenance"] == "observed"
    assert [f["scene_date"] for f in body["frames"]] == ["2026-10-01", "2026-10-16", "2026-10-21"]

    picture = client.get(body["frames"][0]["image_path"], headers=headers)
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/png"
    assert picture.content.startswith(PNG) and "immutable" in picture.headers["cache-control"]


def test_a_missing_frame_or_farm_is_a_404(client, db_session, farm_with_scenes, bucket):
    headers = {"X-API-Key": API_KEY}
    assert client.get(f"/farms/{farm_with_scenes.id}/ndvi-frames/2026-10-06", headers=headers).status_code == 404
    assert client.get("/farms/00000000-0000-0000-0000-000000000000/ndvi-frames", headers=headers).status_code == 404


def test_the_frame_picture_masks_cloud_pixels_per_pixel():
    from app.services.sentinel import CLOUDY_SCL_CLASSES, FRAME_EVALSCRIPT

    assert '"SCL"' in FRAME_EVALSCRIPT and "s.dataMask * clear" in FRAME_EVALSCRIPT
    assert str(list(CLOUDY_SCL_CLASSES)) in FRAME_EVALSCRIPT


# ---- the storage is checked before anything is rendered ----

def test_a_bucket_we_cannot_use_is_reported_before_any_render(db_session, farm_with_scenes, monkeypatch):
    from app.services import ndvi_frames

    monkeypatch.setattr(ndvi_frames.settings, "S3_BUCKET", "aglens-images")

    class Denied:
        def put_object(self, **kw):
            raise RuntimeError("An error occurred (AccessDenied) when calling the PutObject operation: Access Denied")

    monkeypatch.setattr(ndvi_frames, "_get_s3_client", lambda: Denied())

    problem = ndvi_frames.check_storage()
    assert problem and "AccessDenied" in problem


def test_a_working_bucket_passes_the_check(monkeypatch):
    from app.services import ndvi_frames

    monkeypatch.setattr(ndvi_frames.settings, "S3_BUCKET", "aglens-images")
    store = {}

    class Fine:
        def put_object(self, **kw):
            store[kw["Key"]] = kw["Body"]

        def get_object(self, **kw):
            import io
            return {"Body": io.BytesIO(store[kw["Key"]])}

    monkeypatch.setattr(ndvi_frames, "_get_s3_client", lambda: Fine())
    assert ndvi_frames.check_storage() is None


def test_the_backfill_renders_nothing_when_the_bucket_is_unusable(db_session, farm_with_scenes, monkeypatch):
    from app.jobs import backfill_ndvi_frames as job
    from app.services import ndvi_frames

    monkeypatch.setattr(ndvi_frames.settings, "S3_BUCKET", "aglens-images")
    monkeypatch.setattr(job, "check_storage", lambda: "The bucket cannot be written to and read from: AccessDenied")
    monkeypatch.setattr(job, "get_access_token", lambda: pytest.fail("must not even ask Copernicus"))

    summary = job.backfill_ndvi_frames()

    assert summary["frames_saved"] == 0 and "AccessDenied" in summary["storage_problem"]


def test_a_farm_whose_frames_keep_failing_is_left_after_two_attempts(db_session, farm_with_scenes, bucket):
    from app.services.ndvi_frames import save_missing_frames

    bucket.fail_on.update({date(2026, 10, 21), date(2026, 10, 16), date(2026, 10, 1)})
    result = save_missing_frames(db_session, farm_with_scenes, {"type": "Polygon"}, "token")

    assert result == {"saved": 0, "failed": 2} and len(bucket.renders) == 2


def test_a_frame_is_written_without_a_head_check_first(monkeypatch):
    from app.services import ndvi_frames

    monkeypatch.setattr(ndvi_frames.settings, "S3_BUCKET", "aglens-images")
    calls = []

    class OnlyPut:
        def put_object(self, **kw):
            calls.append(("put", kw["Key"], kw["ContentType"]))

    monkeypatch.setattr(ndvi_frames, "_get_s3_client", lambda: OnlyPut())

    assert ndvi_frames.store_png(b"x", "ndvi-frames/f/2026-10-23.png") == "s3://aglens-images/ndvi-frames/f/2026-10-23.png"
    assert calls == [("put", "ndvi-frames/f/2026-10-23.png", "image/png")]


def test_the_frame_list_gives_the_box_the_pictures_cover_so_a_map_can_place_them(client, db_session, farm_with_scenes, bucket):
    from geoalchemy2 import WKTElement

    farm_with_scenes.boundary = WKTElement("POLYGON((3.90 7.60, 3.92 7.60, 3.92 7.63, 3.90 7.63, 3.90 7.60))", srid=4326)
    farm_with_scenes.boundary_mapped = True
    db_session.commit()

    body = client.get(f"/farms/{farm_with_scenes.id}/ndvi-frames", headers={"X-API-Key": API_KEY}).json()

    assert body["bbox"] == [3.90, 7.60, 3.92, 7.63]
    assert body["corners"] == [[3.90, 7.63], [3.92, 7.63], [3.92, 7.60], [3.90, 7.60]]


def test_a_farm_without_a_boundary_has_no_box(client, db_session, farm_with_scenes, bucket):
    body = client.get(f"/farms/{farm_with_scenes.id}/ndvi-frames", headers={"X-API-Key": API_KEY}).json()
    assert body["bbox"] is None and body["corners"] is None
