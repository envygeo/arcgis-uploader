from pathlib import Path
from urllib.parse import urljoin

from fastapi.testclient import TestClient

import app.main as main
from tests.conftest import make_client


def _custom_frontend(monkeypatch, tmp_path: Path) -> TestClient:
    static_dir = tmp_path / "static"
    (static_dir / "assets" / "js").mkdir(parents=True)
    (static_dir / "index.html").write_text(
        '<!doctype html><link href="assets/site.css">'
        '<script src="assets/js/app.js"></script>Custom frontend',
        encoding="utf-8",
    )
    (static_dir / "assets" / "site.css").write_text(
        "body { color: #123456; }", encoding="utf-8"
    )
    (static_dir / "assets" / "js" / "app.js").write_text(
        'document.body.dataset.loaded = "yes";', encoding="utf-8"
    )
    monkeypatch.setattr(main, "STATIC_DIR", static_dir)
    return make_client()


def test_deployment_can_replace_index_and_add_nested_assets(monkeypatch, tmp_path):
    client = _custom_frontend(monkeypatch, tmp_path)

    index = client.get("/")
    script = client.get("/assets/js/app.js")
    stylesheet = client.get("/assets/site.css")

    assert index.status_code == 200
    assert "Custom frontend" in index.text
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert 'dataset.loaded = "yes"' in script.text
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")


def test_missing_frontend_file_returns_404(monkeypatch, tmp_path):
    client = _custom_frontend(monkeypatch, tmp_path)

    response = client.get("/assets/missing.js")

    assert response.status_code == 404


def test_server_routes_take_priority_over_same_named_static_files(
    monkeypatch, tmp_path
):
    client = _custom_frontend(monkeypatch, tmp_path)
    static_api = main.STATIC_DIR / "api"
    static_api.mkdir()
    for name in ("info", "debug-info", "oauth-info", "preview", "upload"):
        (static_api / name).write_text("shadowed", encoding="utf-8")

    assert client.get("/api/info").headers["content-type"].startswith(
        "application/json"
    )
    assert client.get("/api/debug-info").json()["DRY_RUN"] is True
    assert client.get("/api/oauth-info").status_code == 422
    assert client.post("/api/preview").status_code == 422
    assert client.post("/api/upload").status_code == 422
    assert client.app.routes[-1].name == "frontend"


def test_relative_frontend_urls_work_beneath_reverse_proxy_subpath(client):
    proxy_client = TestClient(client.app, root_path="/services/uploader")

    response = proxy_client.get("/")

    assert response.status_code == 200
    base = "https://example.test/services/uploader/"
    assert urljoin(base, "examples.css") == (
        "https://example.test/services/uploader/examples.css"
    )
    assert urljoin(base, "example1.html") == (
        "https://example.test/services/uploader/example1.html"
    )
    assert 'href="example1.html"' in response.text
    example = proxy_client.get("/example1.html")
    stylesheet = proxy_client.get("/examples.css")
    assert example.status_code == 200
    assert 'href="examples.css"' in example.text
    assert stylesheet.status_code == 200


def test_documented_extensionless_example_urls_remain_static_aliases(client):
    aliases = {
        "/example1": "example1.html",
        "/example2": "example2.html",
        "/preview": "example2.html",
        "/example3": "example3.html",
        "/example4": "example4.html",
    }
    for path, target in aliases.items():
        redirect = client.get(path, follow_redirects=False)
        assert redirect.status_code == 307
        wrapper = client.get(redirect.headers["location"])
        assert wrapper.status_code == 200
        assert f"../{target}" in wrapper.text