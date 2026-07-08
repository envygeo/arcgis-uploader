"""Settings, loaded once from environment variables / a .env file.

See .env.example for documentation of every variable.
"""
from __future__ import annotations

import json
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
class DuplicateCompareLayer:
    """A read-only layer to include in duplicate checks.

    ``id_field`` is the field on that layer that should equal the submitted
    project id. The target layer is checked separately through
    ``DUPLICATE_ID_FIELD``.
    """

    id_field: str
    url: str


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
    duplicate_compare_layers: tuple[DuplicateCompareLayer, ...] = ()
    oauth_client_id: str = "arcgispro"
    # How the server gets an ArcGIS edit token:
    # password = ARCGIS_USERNAME/ARCGIS_PASSWORD, iwa = Windows SSPI/IWA
    # using the process identity, anonymous = do not request a token.
    arcgis_auth_mode: str = "password"


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
    compare_layers = (
        os.environ.get("DUPLICATE_COMPARE_LAYERS", "").strip()
        or os.environ.get("COMPARE_LAYERS", "").strip()
    )
    return Settings(
        portal_url=portal,
        arcgis_auth_mode=normalize_arcgis_auth_mode(
            os.environ.get("ARCGIS_AUTH_MODE", "password")
        ),
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
        duplicate_compare_layers=parse_duplicate_compare_layers(compare_layers),
        oauth_client_id=os.environ.get("ARCGIS_OAUTH_CLIENT_ID", "arcgispro").strip()
        or "arcgispro",
    )


def normalize_arcgis_auth_mode(value: str) -> str:
    """Normalize the configured ArcGIS token source."""
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "": "password",
        "builtin": "password",
        "built-in": "password",
        "user-password": "password",
        "username-password": "password",
        "windows": "iwa",
        "windows-iwa": "iwa",
        "integrated-windows": "iwa",
        "sspi": "iwa",
        "none": "anonymous",
        "public": "anonymous",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"password", "iwa", "anonymous"}:
        raise ValueError(
            "ARCGIS_AUTH_MODE must be password, iwa, or anonymous "
            f"(got {value!r})."
        )
    return normalized


def parse_duplicate_compare_layers(value: str) -> tuple[DuplicateCompareLayer, ...]:
    """Parse extra duplicate-check layers from env text.

    Preferred format is JSON because it is unambiguous in a .env file:

    [{"id_field":"registry_project_id","url":"https://.../FeatureServer/3"}]

    For operator convenience this also accepts the older sketch format of one
    ``id_field, url`` pair per line.
    """
    value = value.strip()
    if (
        (value.startswith('"""') and value.endswith('"""'))
        or (value.startswith("'''") and value.endswith("'''"))
    ):
        value = value[3:-3].strip()
    if not value:
        return ()

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return _parse_compare_layer_lines(value)

    if not isinstance(parsed, list):
        raise ValueError("DUPLICATE_COMPARE_LAYERS must be a JSON array.")

    layers: list[DuplicateCompareLayer] = []
    for index, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            raise ValueError(
                f"DUPLICATE_COMPARE_LAYERS item {index} must be an object."
            )
        id_field = str(item.get("id_field") or item.get("field") or "").strip()
        url = str(item.get("url") or "").strip().rstrip("/")
        if not id_field or not url:
            raise ValueError(
                "Each DUPLICATE_COMPARE_LAYERS item needs id_field and url."
            )
        layers.append(DuplicateCompareLayer(id_field=id_field, url=url))
    return tuple(layers)


def _parse_compare_layer_lines(value: str) -> tuple[DuplicateCompareLayer, ...]:
    layers: list[DuplicateCompareLayer] = []
    for line in value.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(","):
            line = line[:-1].rstrip()
        try:
            id_field, url = line.split(",", 1)
        except ValueError as exc:
            raise ValueError(
                "DUPLICATE_COMPARE_LAYERS lines must be 'id_field, url'."
            ) from exc
        id_field = id_field.strip().strip("'\"")
        url = url.strip().strip("'\"").rstrip("/")
        if not id_field or not url:
            raise ValueError(
                "DUPLICATE_COMPARE_LAYERS lines need both id_field and url."
            )
        layers.append(DuplicateCompareLayer(id_field=id_field, url=url))
    return tuple(layers)
