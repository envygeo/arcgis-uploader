from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_release_module():
    path = ROOT / "scripts" / "make_release.py"
    spec = importlib.util.spec_from_file_location("make_release", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_builder_separates_server_and_embed_files(tmp_path: Path) -> None:
    release = load_release_module()

    server_path, embed_path = release.build(tmp_path)

    with zipfile.ZipFile(server_path) as server_zip:
        server_names = set(server_zip.namelist())
        server_config = server_zip.read("config.env.example").decode("utf-8")

    assert "app/main.py" in server_names
    assert "static/example3.html" in server_names
    assert "pyproject.toml" in server_names
    assert "uv.lock" in server_names
    assert "LICENSE" in server_names
    assert "DEPLOY.md" in server_names
    assert "config.env.example" in server_names
    assert "ALLOW_CLIENT_USERNAME=false" in server_config
    assert not any(name.startswith("tests/") for name in server_names)
    assert not any(name.startswith("scripts/") for name in server_names)
    assert ".env" not in server_names
    assert not any(
        "__pycache__" in name or name.endswith(".pyc")
        for name in server_names
    )

    with zipfile.ZipFile(embed_path) as embed_zip:
        embed_names = set(embed_zip.namelist())
        embed_readme = embed_zip.read("README.md").decode("utf-8")

    assert embed_names == {
        "README.md",
        "iframe.html",
        "Example3Uploader.razor",
        "LICENSE",
    }
    assert "/example3" in embed_readme
    assert not any(name.startswith("app/") for name in embed_names)
