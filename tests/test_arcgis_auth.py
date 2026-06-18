import time

import pytest

from app.arcgis import ArcGISClient, ArcGISError
from app.config import Settings


def make_settings(**overrides):
    options = dict(
        portal_url="https://example.test/portal",
        username="svc_user",
        password="secret",
        token_url="https://example.test/portal/sharing/rest/generateToken",
        layer_urls={},
        project_id_field="project_id",
        project_id_pattern=r"^[\w][\w\- .]{0,63}$",
        max_upload_mb=10,
        default_source_epsg=None,
        dry_run=False,
    )
    options.update(overrides)
    return Settings(**options)


class FakeResponse:
    def __init__(self, body, ok=True):
        self._body = body
        self.ok = ok
        self.status_code = 200 if ok else 500

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError("HTTP failed")

    def json(self):
        return self._body


class FakeTokenSession:
    def __init__(self):
        self.headers = {}
        self.calls = []
        self.auth = None

    def request(self, method, url, *, params=None, data=None, timeout=None, headers=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "data": data,
                "timeout": timeout,
                "headers": headers,
            }
        )
        if method == "GET":
            return FakeResponse({"error": {"message": "GET not accepted"}})
        return FakeResponse({"token": "iwa-token", "expires": 4102444800000})


class FakeDeniedSession:
    def __init__(self):
        self.headers = {}
        self.auth = None

    def post(self, url, data=None, timeout=None):
        return FakeResponse(
            {
                "error": {
                    "message": "User does not have permissions",
                    "details": ["to access 'env_yesab/ea_project_areas3.mapserver'."],
                }
            }
        )


def test_iwa_token_uses_windows_auth_without_username_or_password(monkeypatch):
    fake_session = FakeTokenSession()
    monkeypatch.setattr("app.arcgis.requests.Session", lambda: fake_session)
    monkeypatch.setattr(ArcGISClient, "_add_iwa_auth", lambda self: None)

    client = ArcGISClient(make_settings(arcgis_auth_mode="iwa"))

    assert client.token() == "iwa-token"
    assert [call["method"] for call in fake_session.calls] == ["GET", "POST"]
    for call in fake_session.calls:
        assert "username" not in (call["params"] or {})
        assert "password" not in (call["params"] or {})
        assert "username" not in (call["data"] or {})
        assert "password" not in (call["data"] or {})
    post = fake_session.calls[1]
    assert post["data"]["client"] == "referer"
    assert post["data"]["referer"] == "https://example.test/portal"


def test_anonymous_auth_mode_sends_no_token(monkeypatch):
    fake_session = FakeTokenSession()
    monkeypatch.setattr("app.arcgis.requests.Session", lambda: fake_session)

    client = ArcGISClient(make_settings(arcgis_auth_mode="anonymous"))

    assert client.token() is None
    assert fake_session.calls == []


def test_iwa_permission_error_reports_attempted_windows_identity(monkeypatch):
    fake_session = FakeDeniedSession()
    monkeypatch.setattr("app.arcgis.requests.Session", lambda: fake_session)
    monkeypatch.setattr(ArcGISClient, "_add_iwa_auth", lambda self: None)
    monkeypatch.setattr("app.arcgis.windows_identity", lambda: "yg\\jdoe / jdoe@gov.yk.ca")

    client = ArcGISClient(make_settings(arcgis_auth_mode="iwa"))
    client._token = "iwa-token"
    client._token_expires = time.time() + 3600

    with pytest.raises(ArcGISError) as exc_info:
        client.layer_info("https://maps.example.test/server/rest/services/x/FeatureServer/0")

    message = str(exc_info.value)
    assert "ArcGIS IWA attempted Windows identity yg\\jdoe / jdoe@gov.yk.ca" in message
    assert "User does not have permissions" in message
