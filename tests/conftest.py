import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(**overrides) -> TestClient:
    options = dict(
        portal_url="",
        username="",
        password="",
        token_url="",
        layer_urls={},
        project_id_field="project_id",
        project_id_pattern=r"^[\w][\w\- .]{0,63}$",
        max_upload_mb=10,
        default_source_epsg=None,
        dry_run=True,
    )
    options.update(overrides)
    return TestClient(create_app(Settings(**options)))


@pytest.fixture
def client():
    return make_client()


def geojson_bytes() -> bytes:
    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "secret", "owner": "must-be-stripped"},
                "geometry": {"type": "Point", "coordinates": [-135.05, 60.72]},
            },
            {
                "type": "Feature",
                "properties": {"name": "a line"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-135.0, 60.7], [-135.1, 60.8]],
                },
            },
        ],
    }
    return json.dumps(collection).encode()


def post_file(
    client, endpoint: str, content: bytes, filename: str, headers=None, **fields
):
    return client.post(
        endpoint,
        files={"file": (filename, content)},
        data=fields or None,
        headers=headers,
    )
