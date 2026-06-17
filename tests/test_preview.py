"""Tests for the /api/preview endpoint (example 2)."""
import json
import zipfile

import geopandas as gpd
from pyproj import Transformer
from shapely.geometry import Polygon

import app.preview
from tests.conftest import geojson_bytes, post_file


def preview(client, content: bytes, filename: str):
    return post_file(client, "/api/preview", content, filename)


def test_preview_geojson(client):
    response = preview(client, geojson_bytes(), "data.geojson")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["feature_counts"] == {"point": 1, "line": 1}
    assert body["features_skipped_invalid"] == 0
    assert body["geojson_truncated"] is False
    assert len(body["geojson"]["features"]) == 2

    [layer] = body["layers"]
    assert layer["feature_count"] == 2
    assert layer["columns"] == ["name", "owner"]
    assert layer["rows"][0] == ["secret", "must-be-stripped"]
    assert layer["rows"][1] == ["a line", None]  # missing property -> null


def test_preview_reprojects_to_wgs84(client, tmp_path):
    # A small triangle near Whitehorse, expressed in Yukon Albers.
    to_albers = Transformer.from_crs(4326, 3578, always_xy=True)
    x, y = to_albers.transform(-135.05, 60.72)
    gdf = gpd.GeoDataFrame(
        {"size_ha": [12.5], "count": [3]},  # float + int exercise JSON safety
        geometry=[Polygon([(x, y), (x + 1000, y), (x + 1000, y + 1000)])],
        crs="EPSG:3578",
    )
    gdf.to_file(tmp_path / "areas.shp")
    zip_path = tmp_path / "areas.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for part in tmp_path.glob("areas.*"):
            if part != zip_path:
                zf.write(part, part.name)

    response = preview(client, zip_path.read_bytes(), "areas.zip")
    assert response.status_code == 200, response.text
    body = response.json()
    lon, lat = body["geojson"]["features"][0]["geometry"]["coordinates"][0][0]
    assert abs(lon - -135.05) < 0.1 and abs(lat - 60.72) < 0.1
    assert body["layers"][0]["rows"][0] == [12.5, 3]


def test_preview_restores_missing_shapefile_shx(client, tmp_path):
    gdf = gpd.GeoDataFrame(
        {"name": ["area"]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1)])],
        crs="EPSG:4326",
    )
    gdf.to_file(tmp_path / "areas.shp")
    (tmp_path / "areas.shx").unlink()
    zip_path = tmp_path / "areas.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for part in tmp_path.glob("areas.*"):
            if part != zip_path:
                zf.write(part, part.name)

    response = preview(client, zip_path.read_bytes(), "areas.zip")

    assert response.status_code == 200, response.text
    assert response.json()["feature_counts"] == {"polygon": 1}


def test_preview_truncates_map_but_counts_everything(client, monkeypatch):
    monkeypatch.setattr(app.preview, "MAX_PREVIEW_FEATURES", 3)
    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Point", "coordinates": [-135.0 + i / 100, 60.7]},
            }
            for i in range(5)
        ],
    }
    body = preview(client, json.dumps(collection).encode(), "pts.geojson").json()
    assert body["geojson_truncated"] is True
    assert len(body["geojson"]["features"]) == 3
    assert body["feature_counts"] == {"point": 5}  # append still gets all 5


def test_preview_resolves_username_like_upload_does(client):
    body = preview(client, geojson_bytes(), "data.geojson").json()
    assert body["uploaded_by"] == "unknown"

    response = post_file(
        client, "/api/preview", geojson_bytes(), "data.geojson",
        headers={"X-Forwarded-User": "YG\\someone"},
    )
    assert response.json()["uploaded_by"] == "YG\\someone"

    response = post_file(
        client, "/api/preview", geojson_bytes(), "data.geojson",
        username="app-user",
    )
    assert response.json()["uploaded_by"] == "app-user"


def test_preview_rejects_non_spatial(client):
    response = preview(client, b"hello", "notes.csv")
    assert response.status_code == 415
    response = preview(client, b"{}", "empty.geojson")
    assert response.status_code == 422
