import json

import pytest

from app.arcgis import ArcGISAttributeError, ArcGISClient, CHUNK_SIZE
from app.config import ESRI_POINT
from tests.conftest import geojson_bytes, make_client, post_file
from tests.test_arcgis_auth import make_settings


LAYER_URL = "https://example.test/FeatureServer/0"
LAYER_INFO = {
    "geometryType": ESRI_POINT,
    "capabilities": "Create,Query",
    "extent": {"spatialReference": {"wkid": 4326}},
    "fields": [
        {"name": "yesab_id", "type": "esriFieldTypeString", "length": 9},
        {"name": "note", "type": "esriFieldTypeString", "length": 255},
    ],
}


def test_overlong_value_in_later_chunk_prevents_all_inserts(monkeypatch):
    client = ArcGISClient(make_settings())
    client._layer_info[LAYER_URL] = LAYER_INFO
    calls = []
    monkeypatch.setattr(client, "_post", lambda *args, **kwargs: calls.append(args))
    features = [{"attributes": {"yesab_id": "2026-0325"}}] * CHUNK_SIZE
    features.append({"attributes": {"yesab_id": "Q2026_0325"}})

    with pytest.raises(ArcGISAttributeError, match="allows at most 9.*has 10"):
        client.add_features(LAYER_URL, features)

    assert calls == []
    assert features[-1]["attributes"]["yesab_id"] == "Q2026_0325"


@pytest.mark.parametrize("value", ["2026-0325", "short", "", None])
def test_values_within_limit_are_sent_unchanged(monkeypatch, value):
    client = ArcGISClient(make_settings())
    client._layer_info[LAYER_URL] = LAYER_INFO
    calls = []

    def post(url, data):
        calls.append((url, json.loads(data["features"])))
        return {"addResults": [{"success": True}]}

    monkeypatch.setattr(client, "_post", post)
    features = [{"attributes": {"yesab_id": value, "note": "Uploaded by tester."}}]

    assert client.add_features(LAYER_URL, features) == 1
    assert calls == [(LAYER_URL + "/addFeatures", features)]


def test_other_text_fields_and_case_insensitive_lookup(monkeypatch):
    client = ArcGISClient(make_settings())
    client._layer_info[LAYER_URL] = LAYER_INFO
    calls = []
    monkeypatch.setattr(client, "_post", lambda *args, **kwargs: calls.append(args))

    with pytest.raises(ArcGISAttributeError, match="field 'note'.*255.*256"):
        client.add_features(LAYER_URL, [{"attributes": {"NOTE": "x" * 256}}])

    assert calls == []


def test_upload_returns_actionable_422_without_insert(monkeypatch):
    monkeypatch.setattr(ArcGISClient, "layer_info", lambda *args: LAYER_INFO)
    calls = []
    monkeypatch.setattr(ArcGISClient, "_post", lambda *args, **kwargs: calls.append(args))
    client = make_client(
        dry_run=False,
        layer_urls={ESRI_POINT: LAYER_URL},
        project_id_field="YESAB_ID",
        username_field="Note",
        duplicate_detection=False,
    )

    response = post_file(
        client, "/api/upload", geojson_bytes(), "data.geojson",
        project_id="Q2026_0325",
    )

    assert response.status_code == 422
    assert "field 'yesab_id' allows at most 9 characters" in response.json()["detail"]
    assert "submitted value has 10" in response.json()["detail"]
    assert calls == []
