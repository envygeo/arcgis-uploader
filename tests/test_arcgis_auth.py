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
                    "details": ["to access 'hosted/northern_project_areas.mapserver'."],
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
    monkeypatch.setattr("app.arcgis.windows_identity", lambda: "klondike\\jdoe / jdoe@example.gov")

    client = ArcGISClient(make_settings(arcgis_auth_mode="iwa"))
    client._token = "iwa-token"
    client._token_expires = time.time() + 3600

    with pytest.raises(ArcGISError) as exc_info:
        client.layer_info("https://maps.example.test/server/rest/services/x/FeatureServer/0")

    message = str(exc_info.value)
    assert (
        "Account attempted: Windows identity "
        "klondike\\jdoe / jdoe@example.gov" in message
    )
    assert "User does not have permissions" in message


def test_password_permission_error_reports_configured_username(monkeypatch):
    monkeypatch.setattr("app.arcgis.requests.Session", FakeDeniedSession)

    client = ArcGISClient(make_settings(arcgis_auth_mode="password"))
    client._token = "password-token"
    client._token_expires = time.time() + 3600

    with pytest.raises(ArcGISError) as exc_info:
        client.layer_info("https://maps.example.test/server/rest/services/x/FeatureServer/0")

    message = str(exc_info.value)
    assert "Account attempted: configured user svc_user" in message
    assert "User does not have permissions" in message
    assert "secret" not in message
    assert message.startswith("ArcGIS request failed: User does not have permissions")


def test_anonymous_permission_error_reports_anonymous_user(monkeypatch):
    monkeypatch.setattr("app.arcgis.requests.Session", FakeDeniedSession)

    client = ArcGISClient(make_settings(arcgis_auth_mode="anonymous", username=""))

    with pytest.raises(ArcGISError) as exc_info:
        client.layer_info("https://maps.example.test/server/rest/services/x/FeatureServer/0")

    message = str(exc_info.value)
    assert "Account attempted: anonymous user" in message
    assert "User does not have permissions" in message


def test_insert_error_leads_with_failure_not_authentication(monkeypatch):
    client = ArcGISClient(make_settings(arcgis_auth_mode="password"))
    client._layer_info["https://example.test/FeatureServer/0"] = {"fields": []}
    monkeypatch.setattr(client, "token", lambda: "private-token")
    monkeypatch.setattr(
        client.session,
        "post",
        lambda *args, **kwargs: FakeResponse({
            "error": {
                "message": "Unable to complete operation.",
                "details": [
                    "Internal error during object insert.",
                    "Invalid column value [yesab_id]",
                ],
            },
        }),
    )

    with pytest.raises(ArcGISError) as exc_info:
        client.add_features("https://example.test/FeatureServer/0", [{"attributes": {}}])

    assert str(exc_info.value) == (
        "ArcGIS request failed: Unable to complete operation. "
        "Internal error during object insert. Invalid column value [yesab_id]\n"
        "Endpoint: https://example.test/FeatureServer/0/addFeatures\n"
        "Account attempted: configured user svc_user"
    )
