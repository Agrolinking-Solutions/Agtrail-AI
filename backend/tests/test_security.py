"""Tests that need no database."""

import socket

import pytest

from tests.conftest import API_KEY, WEBHOOK_SECRET, sign, load_payload


def test_valid_signature_accepted():
    from app.services.agtrail_webhook_security import verify_webhook_signature

    body = b'{"hello": "world"}'
    assert verify_webhook_signature(body, sign(body))


@pytest.mark.parametrize("header", [None, "", "deadbeef", "é" * 64])
def test_bad_signature_rejected(header):
    from app.services.agtrail_webhook_security import verify_webhook_signature

    assert not verify_webhook_signature(b"{}", header)


def test_signature_over_different_body_rejected():
    from app.services.agtrail_webhook_security import verify_webhook_signature

    assert not verify_webhook_signature(b'{"a": 2}', sign(b'{"a": 1}'))


def test_api_key_accepted_and_labelled():
    from app.core.security import require_api_key

    assert require_api_key(API_KEY) == "tester"


@pytest.mark.parametrize("key", [None, "", "wrong", API_KEY + "x"])
def test_api_key_rejected(key):
    from fastapi import HTTPException

    from app.core.security import require_api_key

    with pytest.raises(HTTPException) as exc:
        require_api_key(key)
    assert exc.value.status_code == 401


def _settings(**overrides):
    from app.core.config import Settings

    values = {
        "SECRET_KEY": "x" * 40,
        "DATABASE_URL": "postgresql://u:p@h/db",
        "AGTRAIL_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "API_KEYS": "a:b",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_rejects_weak_config():
    with pytest.raises(ValueError, match="Unsafe production configuration"):
        _settings(ENVIRONMENT="production", SECRET_KEY="changeme", AGTRAIL_WEBHOOK_SECRET="", API_KEYS="")


def test_production_accepts_strong_config():
    assert _settings(ENVIRONMENT="production").is_production


def test_full_url_in_postgres_host_rejected():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        _settings(DATABASE_URL="", POSTGRES_HOST="postgresql://u:p@h:5432/db", POSTGRES_DB="db", POSTGRES_USER="u")


def test_password_with_special_characters_escaped():
    settings = _settings(
        DATABASE_URL="", POSTGRES_HOST="db.example.com", POSTGRES_DB="app",
        POSTGRES_USER="user", POSTGRES_PASSWORD="p@ss:w/rd?#%",
    )
    url = settings.database_url
    assert url.host == "db.example.com"
    assert url.password == "p@ss:w/rd?#%"
    assert url.query["sslmode"] == "require"


def test_database_url_keeps_its_own_sslmode():
    url = _settings(DATABASE_URL="postgres://u:p@h:5432/db?sslmode=verify-full").database_url
    assert url.drivername == "postgresql+psycopg2"
    assert url.query["sslmode"] == "verify-full"


def _resolve_to(monkeypatch, address):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.1", "169.254.169.254", "0.0.0.0", "100.64.0.1",
])
def test_ssrf_private_addresses_blocked(monkeypatch, address):
    from app.services.safe_fetch import UnsafeURLError, validate_public_url

    _resolve_to(monkeypatch, address)
    with pytest.raises(UnsafeURLError):
        validate_public_url("https://images.example.com/photo.jpg")


def test_ssrf_public_address_allowed(monkeypatch):
    from app.services.safe_fetch import validate_public_url

    _resolve_to(monkeypatch, "93.184.216.34")
    validate_public_url("https://images.example.com/photo.jpg")


@pytest.mark.parametrize("url", [
    "http://images.example.com/photo.jpg",
    "file:///etc/passwd",
    "ftp://images.example.com/photo.jpg",
    "https://user:pass@images.example.com/photo.jpg",
    "https:///no-host",
])
def test_ssrf_bad_urls_blocked(monkeypatch, url):
    from app.services.safe_fetch import UnsafeURLError, validate_public_url

    _resolve_to(monkeypatch, "93.184.216.34")
    with pytest.raises(UnsafeURLError):
        validate_public_url(url)


def test_raw_payload_copies_exclude_farmer_pii():
    from app.schemas.agtrail_webhook import agtrail_webhook_adapter, redacted_payload

    payload = agtrail_webhook_adapter.validate_python(load_payload("test_payload.json"))
    stored = redacted_payload(payload)
    assert "name" not in stored["farmer"]
    assert "phone" not in stored["farmer"]
    assert stored["farmer"]["agtrail_farmer_id"] == "TEST-FARMER-001"


def test_model_file_loads_with_weights_only():
    import torch

    from app.services import inference

    state_dict = torch.load(inference.MODEL_WEIGHTS_PATH, map_location="cpu", weights_only=True)
    assert state_dict
