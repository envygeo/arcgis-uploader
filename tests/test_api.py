"""End-to-end tests against the FastAPI app in dry-run mode (no ArcGIS needed)."""
import json
import zipfile

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from app.config import ESRI_POINT, ESRI_POLYGON, Settings
from app.duplicates import count_duplicate_shapes
from app.ingest import GeometryBuckets
from app.main import DuplicateAppendError, _append
from tests.conftest import geojson_bytes, post_file


def upload(client, content: bytes, filename: str, project_id: str = "2026-0042"):
    return post_file(client, "/api/upload", content, filename, project_id=project_id)


def test_geojson_upload_dry_run(client):
    response = upload(client, geojson_bytes(), "data.geojson")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["features_appended"] == {"point": 1, "line": 1}
    assert body["dry_run"] is True


def test_attributes_are_stripped_and_project_id_assigned(client):
    body = upload(client, geojson_bytes(), "data.geojson").json()
    assert body["sample_feature"]["attributes"] == {
        "project_id": "2026-0042",
        "uploaded_by": "Uploaded by unknown.",  # no proxy header, no username field
    }


def test_zipped_shapefile(client, tmp_path):
    gdf = gpd.GeoDataFrame(
        {"kept_out": ["a", "b"]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1)]), Polygon([(2, 2), (3, 2), (3, 3)])],
        crs="EPSG:3578",  # Yukon Albers, exercises reprojection
    )
    gdf.to_file(tmp_path / "areas.shp")
    zip_path = tmp_path / "areas.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for part in tmp_path.glob("areas.*"):
            if part != zip_path:
                zf.write(part, part.name)

    response = upload(client, zip_path.read_bytes(), "areas.zip")
    assert response.status_code == 200, response.text
    assert response.json()["features_appended"] == {"polygon": 2}


def test_zipped_shapefile_missing_shx_is_restored(client, tmp_path):
    gdf = gpd.GeoDataFrame(
        {"kept_out": ["a"]},
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

    response = upload(client, zip_path.read_bytes(), "areas.zip")

    assert response.status_code == 200, response.text
    assert response.json()["features_appended"] == {"polygon": 1}


def test_append_adds_default_z_for_target_layer_with_z():
    class Client:
        def __init__(self):
            self.features = None

        def validate_layer(self, layer_url, esri_type, required_fields):
            assert layer_url == "https://example.test/layer/0"
            assert esri_type == ESRI_POLYGON
            return {name: name for name in required_fields}

        def layer_wkid(self, layer_url):
            return 4326

        def layer_has_z(self, layer_url):
            return True

        def add_features(self, layer_url, features):
            self.features = features
            return len(features)

    settings = Settings(
        portal_url="",
        username="",
        password="",
        token_url="",
        layer_urls={ESRI_POLYGON: "https://example.test/layer/0"},
        project_id_field="project_id",
        project_id_pattern=r"^[\w][\w\- .]{0,63}$",
        max_upload_mb=10,
        default_source_epsg=None,
        dry_run=False,
        duplicate_detection=False,
    )
    buckets = GeometryBuckets(
        by_family={
            ESRI_POLYGON: [
                gpd.GeoSeries([Polygon([(0, 0), (1, 0), (1, 1)])], crs="EPSG:4326")
            ]
        },
        layers=["input:areas"],
        read=1,
    )
    client = Client()

    result = _append(buckets, "2026-0042", "tester", settings, client)

    assert result["features_appended"] == {"polygon": 1}
    geometry = client.features[0]["geometry"]
    assert client.features[0]["attributes"]["uploaded_by"] == "Uploaded by tester."
    assert geometry["hasZ"] is True
    ring = geometry["rings"][0]
    assert all(len(vertex) == 3 for vertex in ring)
    assert all(vertex[2] == 0.0 for vertex in ring)


def test_duplicate_detection_uses_one_metre_precision():
    outgoing = [
        {
            "geometry": {
                "x": -135.05,
                "y": 60.72,
                "spatialReference": {"wkid": 4326},
            },
            "attributes": {},
        }
    ]
    within_one_metre = [
        {
            "x": -135.05,
            "y": 60.720004,
            "spatialReference": {"wkid": 4326},
        }
    ]
    over_one_metre = [
        {
            "x": -135.05,
            "y": 60.72002,
            "spatialReference": {"wkid": 4326},
        }
    ]

    assert count_duplicate_shapes(outgoing, within_one_metre, 4326, 1.0) == 1
    assert count_duplicate_shapes(outgoing, over_one_metre, 4326, 1.0) == 0


def test_append_refuses_duplicate_shape_with_same_id():
    class Client:
        def validate_layer(self, layer_url, esri_type, required_fields):
            assert required_fields == ["yesab_id", "uploaded_by"]
            return {name: name for name in required_fields}

        def layer_wkid(self, layer_url):
            return 4326

        def layer_has_z(self, layer_url):
            return False

        def duplicate_geometries(self, layer_url, id_field, id_value, wkid):
            assert id_field == "yesab_id"
            assert id_value == "YESAB-123"
            return [{"x": -135.05, "y": 60.72, "spatialReference": {"wkid": wkid}}]

        def add_features(self, layer_url, features):
            raise AssertionError("duplicate features should not be appended")

    settings = Settings(
        portal_url="",
        username="",
        password="",
        token_url="",
        layer_urls={ESRI_POINT: "https://example.test/layer/0"},
        project_id_field="yesab_id",
        project_id_pattern=r"^[\w][\w\- .]{0,63}$",
        max_upload_mb=10,
        default_source_epsg=None,
        dry_run=False,
        duplicate_detection=True,
        duplicate_id_field="yesab_id",
        duplicate_tolerance_m=1.0,
    )
    buckets = GeometryBuckets(
        by_family={
            ESRI_POINT: [gpd.GeoSeries([Point(-135.05, 60.72)], crs="EPSG:4326")]
        },
        layers=["input:sites"],
        read=1,
    )

    with pytest.raises(DuplicateAppendError, match="append refused"):
        _append(buckets, "YESAB-123", "tester", settings, Client())


def test_bad_project_id_rejected(client):
    response = upload(client, geojson_bytes(), "data.geojson", project_id="../../etc")
    assert response.status_code == 422


def test_unsupported_extension_rejected(client):
    response = upload(client, b"id,x,y\n1,2,3\n", "data.csv")
    assert response.status_code == 415


def test_garbage_zip_rejected(client):
    response = upload(client, b"not actually a zip", "data.zip")
    assert response.status_code == 422


def test_no_spatial_content_rejected(client, tmp_path):
    zip_path = tmp_path / "docs.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("readme.txt", "nothing spatial here")
    response = upload(client, zip_path.read_bytes(), "docs.zip")
    assert response.status_code == 422


def test_geometry_collection_and_multipoint(client):
    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "MultiPoint",
                    "coordinates": [[-135.0, 60.7], [-135.1, 60.8]],
                },
            }
        ],
    }
    body = upload(client, json.dumps(collection).encode(), "pts.geojson").json()
    # multipoint explodes into individual points
    assert body["features_appended"] == {"point": 2}
