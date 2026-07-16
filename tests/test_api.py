"""End-to-end tests against the FastAPI app in dry-run mode (no ArcGIS needed)."""
import json
import zipfile

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from app.config import (
    ESRI_POINT,
    ESRI_POLYGON,
    ESRI_POLYLINE,
    DuplicateCompareLayer,
    Settings,
)
from app.duplicates import count_duplicate_shapes
from app.ingest import GeometryBuckets
from app.main import DuplicateAppendError, _append
from tests.conftest import geojson_bytes, make_client, post_file


def upload(client, content: bytes, filename: str, project_id: str = "2026-0042"):
    return post_file(client, "/api/upload", content, filename, project_id=project_id)


def test_geojson_upload_dry_run(client):
    response = upload(client, geojson_bytes(), "data.geojson")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["features_appended"] == {"point": 1, "line": 1}
    assert body["feature_layer_urls"] == {}
    assert body["dry_run"] is True


def test_example_pages_exist(client):
    pages = {
        "/": "ArcGIS uploader examples",
        "/example1": "example 1: one-step form",
        "/preview": "example 2: preview &amp; confirm",
        "/example2": "example 2: preview &amp; confirm",
        "/example3": "example 3: preview + duplicate check",
        "/example4": "example 4: browser SSO token",
    }
    for path, expected in pages.items():
        response = client.get(path)
        assert response.status_code == 200
        assert expected in response.text


def test_every_page_links_to_parent_directory(client):
    for path in (
        "/", "/example1", "/preview", "/example2", "/example3", "/example4"
    ):
        response = client.get(path)

        assert response.status_code == 200
        assert '<a href="../">go up one parent dir</a>' in response.text

def test_index_links_to_all_example_flows(client):
    response = client.get("/")

    assert response.status_code == 200
    for number in range(1, 5):
        assert f' href="example{number}"' in response.text
        assert f"Example {number}" in response.text


def test_shared_example_stylesheet_is_served(client):
    response = client.get("/examples.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert "--accent: #1c6e8c" in response.text
    assert ".debug-link" in response.text


def test_example_pages_link_to_allowlisted_debug_info(client):
    for path in ("/example1", "/example2", "/example3", "/example4"):
        response = client.get(path)

        assert response.status_code == 200
        assert 'href="api/debug-info"' in response.text
        assert "Show debug info" in response.text


def test_example_pages_link_to_appended_feature_layers(client):
    for path in ("/example1", "/example2", "/example3", "/example4"):
        response = client.get(path)

        assert response.status_code == 200
        assert "data.feature_layer_urls?.[type]" in response.text
        assert "view ${escapeHtml(type)} feature layer" in response.text
        assert 'target="_blank" rel="noopener"' in response.text


def test_debug_info_reports_effective_settings_without_secrets(monkeypatch):
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")
    client = make_client(
        portal_url="https://portal.example.test/arcgis",
        arcgis_auth_mode="iwa",
        username="configured-user",
        password="super-secret-password",
        token_url="https://portal.example.test/arcgis/sharing/rest/generateToken",
        layer_urls={
            ESRI_POINT: "https://services.example.test/FeatureServer/0",
            ESRI_POLYGON: "https://services.example.test/FeatureServer/2",
        },
        project_id_field="review_id",
        project_id_pattern=r"^YT-[0-9]+$",
        max_upload_mb=25,
        default_source_epsg=3578,
        dry_run=False,
        basemap_url="https://tiles.example.test/{z}/{y}/{x}",
        username_field="submitted_by",
        username_header="X-Authenticated-User",
        allow_client_username=False,
        duplicate_detection=True,
        duplicate_id_field="registry_id",
        duplicate_tolerance_m=0.25,
        duplicate_compare_layers=(
            DuplicateCompareLayer(
                id_field="external_id",
                url="https://reference.example.test/FeatureServer/4",
            ),
        ),
        oauth_client_id="uploader-app",
    )

    response = client.get("/api/debug-info")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "PORTAL_URL",
        "ARCGIS_AUTH_MODE",
        "ARCGIS_USERNAME",
        "ARCGIS_PASSWORD",
        "ARCGIS_OAUTH_CLIENT_ID",
        "GENERATE_TOKEN_URL",
        "TARGET_LAYER_POINT",
        "TARGET_LAYER_POLYLINE",
        "TARGET_LAYER_POLYGON",
        "PROJECT_ID_FIELD",
        "USERNAME_FIELD",
        "USERNAME_HEADER",
        "ALLOW_CLIENT_USERNAME",
        "PROJECT_ID_PATTERN",
        "DUPLICATE_DETECTION",
        "DUPLICATE_ID_FIELD",
        "DUPLICATE_TOLERANCE_M",
        "DUPLICATE_COMPARE_LAYERS",
        "MAX_UPLOAD_MB",
        "DEFAULT_SOURCE_EPSG",
        "SHAPE_RESTORE_SHX",
        "DRY_RUN",
        "BASEMAP_URL",
    }
    assert body["PORTAL_URL"] == "https://portal.example.test/arcgis"
    assert body["ARCGIS_AUTH_MODE"] == "iwa"
    assert body["ARCGIS_USERNAME"] == "configured-user"
    assert body["ARCGIS_PASSWORD"] == {"set": True}
    assert body["ARCGIS_OAUTH_CLIENT_ID"] == "uploader-app"
    assert body["GENERATE_TOKEN_URL"].endswith("/sharing/rest/generateToken")
    assert body["TARGET_LAYER_POINT"].endswith("/FeatureServer/0")
    assert body["TARGET_LAYER_POLYLINE"] == ""
    assert body["TARGET_LAYER_POLYGON"].endswith("/FeatureServer/2")
    assert body["PROJECT_ID_FIELD"] == "review_id"
    assert body["USERNAME_FIELD"] == "submitted_by"
    assert body["USERNAME_HEADER"] == "X-Authenticated-User"
    assert body["ALLOW_CLIENT_USERNAME"] is False
    assert body["PROJECT_ID_PATTERN"] == r"^YT-[0-9]+$"
    assert body["DUPLICATE_DETECTION"] is True
    assert body["DUPLICATE_ID_FIELD"] == "registry_id"
    assert body["DUPLICATE_TOLERANCE_M"] == 0.25
    assert body["DUPLICATE_COMPARE_LAYERS"] == [
        {
            "id_field": "external_id",
            "url": "https://reference.example.test/FeatureServer/4",
        }
    ]
    assert body["MAX_UPLOAD_MB"] == 25
    assert body["DEFAULT_SOURCE_EPSG"] == 3578
    assert body["SHAPE_RESTORE_SHX"] == "YES"
    assert body["DRY_RUN"] is False
    assert body["BASEMAP_URL"] == "https://tiles.example.test/{z}/{y}/{x}"

    serialized = json.dumps(body)
    assert "super-secret-password" not in serialized
    assert "UNRELATED_SECRET" not in serialized
    assert "must-not-leak" not in serialized
    assert "token" not in {key.lower() for key in body}


def test_debug_info_sanitizes_credentials_and_sensitive_url_queries():
    client = make_client(
        portal_url=(
            "https://portal-user:portal-password@portal.example.test/arcgis"
            "?token=portal-token&access_token=access-token&view=full"
        ),
        token_url=(
            "https://token-user:token-password@portal.example.test/generate"
            "?api_key=api-key"
        ),
        layer_urls={
            ESRI_POINT: "https://layer.example.test/0?key=key-value",
            ESRI_POLYLINE: "https://layer.example.test/1?password=query-password",
            ESRI_POLYGON: "https://layer.example.test/2?secret=query-secret",
        },
        basemap_url=(
            "https://tiles.example.test/{z}/{y}/{x}"
            "?signature=tile-signature&style=day"
        ),
        duplicate_compare_layers=(
            DuplicateCompareLayer(
                id_field="external_id",
                url=(
                    "https://compare-user:compare-password@reference.example.test/4"
                    "?client_secret=client-secret&visible=yes"
                ),
            ),
        ),
    )

    response = client.get("/api/debug-info")

    assert response.status_code == 200
    body = response.json()
    assert body["PORTAL_URL"] == (
        "https://portal.example.test/arcgis"
        "?token=REDACTED&access_token=REDACTED&view=full"
    )
    assert body["GENERATE_TOKEN_URL"] == (
        "https://portal.example.test/generate?api_key=REDACTED"
    )
    assert body["TARGET_LAYER_POINT"].endswith("?key=REDACTED")
    assert body["TARGET_LAYER_POLYLINE"].endswith("?password=REDACTED")
    assert body["TARGET_LAYER_POLYGON"].endswith("?secret=REDACTED")
    assert body["BASEMAP_URL"].endswith("?signature=REDACTED&style=day")
    assert body["DUPLICATE_COMPARE_LAYERS"][0]["url"] == (
        "https://reference.example.test/4"
        "?client_secret=REDACTED&visible=yes"
    )

    serialized = json.dumps(body)
    for secret in (
        "portal-user",
        "portal-password",
        "portal-token",
        "access-token",
        "token-user",
        "token-password",
        "api-key",
        "key-value",
        "query-password",
        "query-secret",
        "tile-signature",
        "compare-user",
        "compare-password",
        "client-secret",
    ):
        assert secret not in serialized


def test_debug_info_reports_unset_password(client):
    response = client.get("/api/debug-info")

    assert response.status_code == 200
    assert response.json()["ARCGIS_PASSWORD"] == {"set": False}


def test_example4_does_not_send_user_typed_username(client):
    response = client.get("/example4")

    assert response.status_code == 200
    assert 'name="username"' not in response.text
    assert 'body.append("username"' not in response.text
    assert 'fetch("api/upload-browser-sso"' in response.text
    assert "Username is informational in example 4" in response.text


def test_example4_browser_sso_upload_ignores_form_username_in_dry_run(client):
    response = post_file(
        client,
        "/api/upload-browser-sso",
        geojson_bytes(),
        "data.geojson",
        project_id="2026-0042",
        oauth_session="dry-run",
        username="spoofed",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["uploaded_by"] == "unknown"
    assert body["sample_feature"]["attributes"]["uploaded_by"] == "Uploaded by unknown."


def test_oauth_info_uses_configured_portal_and_oob_redirect(client):
    client = make_client(portal_url="https://example.test/portal")
    response = client.get("/api/oauth-info")

    assert response.status_code == 200
    body = response.json()
    assert "sharing/rest/oauth2/authorize" in body["authorize_url"]
    assert "redirect_uri=urn%3Aietf%3Awg%3Aoauth%3A2.0%3Aoob" in body["authorize_url"]
    assert body["client_id"] == "arcgispro"


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
    assert result["feature_layer_urls"] == {
        "polygon": "https://example.test/layer/0"
    }
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
            assert required_fields == ["review_id", "uploaded_by"]
            return {name: name for name in required_fields}

        def layer_wkid(self, layer_url):
            return 4326

        def layer_has_z(self, layer_url):
            return False

        def duplicate_geometries(self, layer_url, id_field, id_value, wkid):
            assert id_field == "review_id"
            assert id_value == "YT-REV-123"
            return [{"x": -135.05, "y": 60.72, "spatialReference": {"wkid": wkid}}]

        def add_features(self, layer_url, features):
            raise AssertionError("duplicate features should not be appended")

    settings = Settings(
        portal_url="",
        username="",
        password="",
        token_url="",
        layer_urls={ESRI_POINT: "https://example.test/layer/0"},
        project_id_field="review_id",
        project_id_pattern=r"^[\w][\w\- .]{0,63}$",
        max_upload_mb=10,
        default_source_epsg=None,
        dry_run=False,
        duplicate_detection=True,
        duplicate_id_field="review_id",
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
        _append(buckets, "YT-REV-123", "tester", settings, Client())


def test_append_checks_duplicate_compare_layers_with_their_own_id_field():
    compare_url = "https://example.test/FeatureServer/3"

    class Client:
        def validate_layer(self, layer_url, esri_type, required_fields):
            assert layer_url == "https://example.test/target/0"
            assert required_fields == ["REVIEW_ID", "uploaded_by"]
            return {name: name for name in required_fields}

        def layer_wkid(self, layer_url):
            return 4326

        def layer_has_z(self, layer_url):
            return False

        def duplicate_geometries(self, layer_url, id_field, id_value, wkid):
            assert id_value == "2026-0042"
            if layer_url == "https://example.test/target/0":
                assert id_field == "REVIEW_ID"
                return []
            if layer_url == compare_url:
                assert id_field == "registry_project_id"
                return [
                    {
                        "rings": [
                            [[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]
                        ],
                        "spatialReference": {"wkid": wkid},
                    }
                ]
            raise AssertionError(f"unexpected duplicate layer: {layer_url}")

        def add_features(self, layer_url, features):
            raise AssertionError("duplicate features should not be appended")

    settings = Settings(
        portal_url="",
        username="",
        password="",
        token_url="",
        layer_urls={ESRI_POLYGON: "https://example.test/target/0"},
        project_id_field="REVIEW_ID",
        project_id_pattern=r"^[\w][\w\- .]{0,63}$",
        max_upload_mb=10,
        default_source_epsg=None,
        dry_run=False,
        duplicate_detection=True,
        duplicate_tolerance_m=1.0,
        duplicate_compare_layers=(
            DuplicateCompareLayer("registry_project_id", compare_url),
        ),
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

    with pytest.raises(DuplicateAppendError, match="2 checked layer"):
        _append(buckets, "2026-0042", "tester", settings, Client())


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
