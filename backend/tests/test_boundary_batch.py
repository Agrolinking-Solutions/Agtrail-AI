"""
AgTrail rejects a whole boundary batch when it holds one id it cannot read. The client
splits a rejected batch to isolate that id, so the other farms still get their boundary.
No database or network is used.
"""

import json

import httpx

import app.services.agtrail_boundary_client as client


class Response:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text = status, body or {}, text

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)


def fake_post(bad_ids=(), unknown_ids=(), calls=None):
    def post(url, content, headers, timeout):
        ids = json.loads(content)["farmIds"]
        if calls is not None:
            calls.append(list(ids))
        if any(i in bad_ids for i in ids):
            return Response(400, text='{"success":false,"message":"Invalid farm id"}')
        known = [i for i in ids if i not in unknown_ids]
        items = [{"farmId": i, "mapped": True} for i in known]
        return Response(200, {"data": {"items": items, "missingIds": [i for i in ids if i in unknown_ids]}})

    return post


def ids(n):
    return [f"farm-{i}" for i in range(n)]


def test_a_clean_batch_is_one_call(monkeypatch):
    calls = []
    monkeypatch.setattr(client.httpx, "post", fake_post(calls=calls))

    items, missing = client.get_farm_boundaries_batch(ids(100))

    assert len(items) == 100 and missing == [] and len(calls) == 1


def test_one_bad_id_only_loses_itself_not_the_whole_batch(monkeypatch):
    batch = ["TEST-FARM-001"] + ids(99)
    monkeypatch.setattr(client.httpx, "post", fake_post(bad_ids={"TEST-FARM-001"}))

    items, missing = client.get_farm_boundaries_batch(batch)

    assert missing == ["TEST-FARM-001"]
    assert sorted(i["farmId"] for i in items) == sorted(ids(99))


def test_several_bad_ids_are_all_isolated_and_the_rest_still_sync(monkeypatch):
    batch = ["TEST-FARM-001", "TEST-FARM-002"] + ids(98)
    monkeypatch.setattr(client.httpx, "post", fake_post(bad_ids={"TEST-FARM-001", "TEST-FARM-002"}))

    items, missing = client.get_farm_boundaries_batch(batch)

    assert sorted(missing) == ["TEST-FARM-001", "TEST-FARM-002"] and len(items) == 98


def test_isolating_a_bad_id_takes_a_handful_of_calls_not_one_per_farm(monkeypatch):
    calls = []
    monkeypatch.setattr(client.httpx, "post", fake_post(bad_ids={"bad"}, calls=calls))

    client.get_farm_boundaries_batch(["bad"] + ids(99))

    assert len(calls) < 20


def test_ids_agtrail_does_not_know_are_still_reported_as_missing(monkeypatch):
    monkeypatch.setattr(client.httpx, "post", fake_post(unknown_ids={"farm-3"}))

    items, missing = client.get_farm_boundaries_batch(ids(5))

    assert missing == ["farm-3"] and len(items) == 4


def test_a_single_rejected_id_is_missing_and_a_network_error_still_reports_the_batch_as_missing(monkeypatch):
    monkeypatch.setattr(client.httpx, "post", fake_post(bad_ids={"bad"}))
    assert client.get_farm_boundaries_batch(["bad"]) == ([], ["bad"])

    def broken(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(client.httpx, "post", broken)
    assert client.get_farm_boundaries_batch(["a", "b"]) == ([], ["a", "b"])
