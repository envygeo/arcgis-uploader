"""How the uploaded_by attribute gets its value.

Preference order: `username` form field (the calling app's user, unless
ALLOW_CLIENT_USERNAME=false) -> SSO proxy header -> "unknown".
"""
from tests.conftest import geojson_bytes, make_client, post_file


def attributes(response):
    assert response.status_code == 200, response.text
    return response.json()["sample_feature"]["attributes"]


def test_proxy_header(client):
    response = post_file(
        client, "/api/upload", geojson_bytes(), "data.geojson",
        headers={"X-Forwarded-User": "YG\\mwilkie"},
        project_id="2026-0042",
    )
    assert attributes(response)["uploaded_by"] == "Uploaded by YG\\mwilkie."
    assert response.json()["uploaded_by"] == "YG\\mwilkie"
    assert response.json()["username_attribute_value"] == "Uploaded by YG\\mwilkie."


def test_form_field_from_calling_app_outranks_header(client):
    response = post_file(
        client, "/api/upload", geojson_bytes(), "data.geojson",
        headers={"X-Forwarded-User": "proxy-user"},
        project_id="2026-0042",
        username="app-user@example.gov",
    )
    assert attributes(response)["uploaded_by"] == "Uploaded by app-user@example.gov."


def test_form_field_ignored_when_disallowed():
    client = make_client(allow_client_username=False)
    response = post_file(
        client, "/api/upload", geojson_bytes(), "data.geojson",
        headers={"X-Forwarded-User": "proxy-user"},
        project_id="2026-0042",
        username="spoofed",
    )
    assert attributes(response)["uploaded_by"] == "Uploaded by proxy-user."


def test_custom_header_name():
    client = make_client(username_header="X-Remote-User")
    response = post_file(
        client, "/api/upload", geojson_bytes(), "data.geojson",
        headers={"X-Remote-User": "iis-user"},
        project_id="2026-0042",
    )
    assert attributes(response)["uploaded_by"] == "Uploaded by iis-user."


def test_custom_username_field_gets_uploaded_by_sentence():
    client = make_client(username_field="Note")
    response = post_file(
        client,
        "/api/upload",
        geojson_bytes(),
        "data.geojson",
        project_id="2026-0042",
        username="app-user",
    )
    assert attributes(response)["Note"] == "Uploaded by app-user."
