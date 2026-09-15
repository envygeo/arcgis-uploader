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
    assert "window.addPreviewContext(map, serverInfo || {})" in page.text
    assert "maxZoom: 18" in page.text
    assert "L.tileLayer(serverInfo.basemap_url" not in page.text
    assert "https://unpkg.com" not in page.text


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


def test_context_browser_behavior():
    node = shutil.which("node")
    assert node is not None, "Node.js is required for JavaScript behavior tests"
    result = subprocess.run(
        [node, "--test", "tests/preview_context.test.cjs"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
