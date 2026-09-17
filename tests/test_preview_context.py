from pathlib import Path
import shutil
import subprocess

import pytest

from app.config import load_settings
from tests.conftest import make_client


ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name", ["example2", "example3", "example4"])
def test_preview_pages_use_shared_local_map_assets(client, name):
    page = client.get(f"/{name}.html")
    for asset in ("leaflet.js", "leaflet.css", "esri-leaflet.js", "preview-context.js"):
        assert f'"assets/{asset}"' in page.text
        assert client.get(f"/assets/{asset}").status_code == 200
    assert 'window.createPreviewMap("map", serverInfo)' in page.text
    assert "L.tileLayer(serverInfo.basemap_url" not in page.text
    assert "https://unpkg.com" not in page.text


def test_wide_preview_tables_are_contained_by_the_upload_card(client):
    styles = client.get("/assets/styles.css").text

    assert ".card > *," in styles
    assert "#preview > *," in styles
    assert "#tables {" in styles
    assert ".table-wrap {" in styles
    assert "max-width: 100%;" in styles
    assert "overflow-x: auto;" in styles
    assert "min-width: max-content;" in styles


def test_basemap_attribution_configuration(monkeypatch):
    monkeypatch.setenv("BASEMAP_URL", " https://tiles.example.test/{z}/{x}/{y} ")
    monkeypatch.setenv("BASEMAP_ATTRIBUTION", " Example provider ")
    settings = load_settings()
    assert settings.basemap_attribution == "Example provider"
    info = make_client(
        basemap_url=settings.basemap_url,
        basemap_attribution=settings.basemap_attribution,
    ).get("/api/info").json()
    assert info["basemap_url"] == "https://tiles.example.test/{z}/{x}/{y}"
    assert info["basemap_attribution"] == "Example provider"
    assert info["preview_map"]["basemap"]["url"] == info["basemap_url"]
    assert info["preview_map"]["basemap"]["type"] == "xyz_tiles"
    assert info["preview_map"]["basemap"]["source"] == "BASEMAP_URL override"
    assert info["preview_map"]["basemap"]["attribution"] == "Example provider"


def test_debug_info_shows_effective_map_defaults_used_by_frontend(client):
    debug = client.get("/api/debug-info").json()
    config = client.get("/api/info").json()["preview_map"]
    assert debug["BASEMAP_URL"] == ""
    assert debug["PREVIEW_MAP"] == config
    assert config["basemap"]["url"].endswith("/Yukon_Basemap_Cache/MapServer")
    assert config["basemap"]["type"] == "dynamic_map"
    assert config["basemap"]["source"] == "built-in default"
    assert config["basemap"]["attribution_source"] == "service metadata"
    assert config["context"]["url"].endswith("/GeoYukon/GY_Mining/MapServer")
    assert [g["ids"] for g in config["context"]["groups"]] == [[35, 36], [10, 11], [39], [16]]
    assert all(g["enabled"] for g in config["context"]["groups"])
    assert config["context"]["opacity"] == 0.65
    assert config["view"] == {"center": [63.5, -135.5], "zoom": 5, "min_zoom": 5, "max_zoom": 18}
    assert config["request_timeout_ms"] == 15000
    assert debug["DUPLICATE_ID_FIELD"] == debug["PROJECT_ID_FIELD"] == "project_id"
    assert debug["DUPLICATE_TOLERANCE_M"] == 1.0
    assert debug["USERNAME_HEADER"] == "X-Forwarded-User"


def test_effective_map_debug_redacts_override_without_mutating_frontend_config():
    url = "https://user:password@tiles.example.test/{z}/{x}/{y}?token=secret"
    client = make_client(basemap_url=url, basemap_attribution='<a href="?key=secret">Provider</a>')
    debug = client.get("/api/debug-info").json()
    basemap = debug["PREVIEW_MAP"]["basemap"]
    assert basemap["url"] == "https://tiles.example.test/{z}/{x}/{y}?token=REDACTED"
    assert basemap["source"] == "BASEMAP_URL override"
    assert basemap["attribution_source"] == "BASEMAP_ATTRIBUTION override"
    assert basemap["attribution"] == "[configured HTML omitted]"
    assert "secret" not in str(debug)
    assert client.get("/api/info").json()["preview_map"]["basemap"]["url"] == url
    assert make_client().get("/api/info").json()["preview_map"]["basemap"]["type"] == "dynamic_map"


def test_context_browser_behavior():
    node = shutil.which("node")
    assert node is not None, "Node.js is required for JavaScript behavior tests"
    result = subprocess.run(
        [node, "--test", "tests/preview_context.test.cjs"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
