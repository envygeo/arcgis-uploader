"""Settings, loaded once from environment variables / a .env file.

See .env.example for documentation of every variable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

ESRI_POINT = "esriGeometryPoint"
ESRI_POLYLINE = "esriGeometryPolyline"
ESRI_POLYGON = "esriGeometryPolygon"

# Human-readable names used in API responses.
FAMILY_LABELS = {
    ESRI_POINT: "point",
    ESRI_POLYLINE: "line",
    ESRI_POLYGON: "polygon",
}


@dataclass(frozen=True)
class Settings:
    portal_url: str
    username: str
    password: str
    token_url: str
    layer_urls: dict[str, str]  # Esri geometry type -> feature layer URL
    project_id_field: str
    project_id_pattern: str
    max_upload_mb: int
    default_source_epsg: int | None
    dry_run: bool
    basemap_url: str = ""  # optional XYZ tile template for the preview map
    username_field: str = "uploaded_by"
    # Header carrying the authenticated user, set by the SSO/reverse proxy in
    # front of this app. Browsers cannot (and must not) supply it themselves.
    username_header: str = "X-Forwarded-User"
    # Whether to accept a `username` form field from the calling app (a
    # third-party client that already authenticated its user). The field
    # outranks the header when allowed.
    allow_client_username: bool = True
    # Refuse appends when the same id field and shape already exist in the
    # destination layer. Geometry is compared in metres after reprojection.
    duplicate_detection: bool = True
    duplicate_id_field: str = ""
    duplicate_tolerance_m: float = 1.0


def load_settings() -> Settings:
    load_dotenv()
    portal = os.environ.get("PORTAL_URL", "").strip().rstrip("/")
    layer_urls = {}
    for env_name, esri_type in [
        ("TARGET_LAYER_POINT", ESRI_POINT),
        ("TARGET_LAYER_POLYLINE", ESRI_POLYLINE),
        ("TARGET_LAYER_POLYGON", ESRI_POLYGON),
    ]:
        url = os.environ.get(env_name, "").strip().rstrip("/")
        if url:
            layer_urls[esri_type] = url
    default_epsg = os.environ.get("DEFAULT_SOURCE_EPSG", "").strip()
    project_id_field = os.environ.get("PROJECT_ID_FIELD", "project_id").strip()
    return Settings(
        portal_url=portal,
        username=os.environ.get("ARCGIS_USERNAME", "").strip(),
        password=os.environ.get("ARCGIS_PASSWORD", ""),
        token_url=os.environ.get("GENERATE_TOKEN_URL", "").strip()
        or (f"{portal}/sharing/rest/generateToken" if portal else ""),
        layer_urls=layer_urls,
        project_id_field=project_id_field,
        project_id_pattern=os.environ.get("PROJECT_ID_PATTERN", r"^[\w][\w\- .]{0,63}$"),
        max_upload_mb=int(os.environ.get("MAX_UPLOAD_MB", "200")),
        default_source_epsg=int(default_epsg) if default_epsg else None,
        dry_run=os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes"),
        basemap_url=os.environ.get("BASEMAP_URL", "").strip(),
        username_field=os.environ.get("USERNAME_FIELD", "uploaded_by").strip(),
        username_header=os.environ.get("USERNAME_HEADER", "X-Forwarded-User").strip(),
        allow_client_username=os.environ.get("ALLOW_CLIENT_USERNAME", "true")
        .strip()
        .lower()
        not in ("0", "false", "no"),
        duplicate_detection=os.environ.get("DUPLICATE_DETECTION", "true")
        .strip()
        .lower()
        not in ("0", "false", "no"),
        duplicate_id_field=os.environ.get("DUPLICATE_ID_FIELD", project_id_field)
        .strip(),
        duplicate_tolerance_m=float(os.environ.get("DUPLICATE_TOLERANCE_M", "1.0")),
    )
