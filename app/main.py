"""Reference web service: upload spatial data, strip attributes, tag with a
project ID, append to an ArcGIS Enterprise hosted feature service.

Pipeline (one POST /api/upload request):
  save upload -> read with GDAL -> discard attributes, normalize dimensions ->
  reproject to the target layer's spatial reference ->
  tag every feature with project_id -> addFeatures via ArcGIS REST.
"""
from __future__ import annotations

import logging
import re
import secrets
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pyproj import CRS
from pyproj.exceptions import CRSError
import requests

from .arcgis import ArcGISClient, ArcGISError
from .config import FAMILY_LABELS, Settings, load_settings
from .duplicates import count_duplicate_shapes
from .esri import to_esri_geometry
from .ingest import ACCEPTED_UPLOADS, GeometryBuckets, IngestError, collect_geometries
from .preview import build_preview

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
OAUTH_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"

logger = logging.getLogger("arcgis-uploader")

USERNAME_ATTRIBUTE_PREFIX = "Uploaded by "
USERNAME_ATTRIBUTE_SUFFIX = "."
USERNAME_ATTRIBUTE_MAX_LEN = 128


class DuplicateAppendError(ValueError):
    """The upload would duplicate an existing id + Shape; safe to show."""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    client = ArcGISClient(settings)
    iwa_client: ArcGISClient | None = None
    oauth_sessions: dict[str, dict] = {}
    app = FastAPI(title="arcgis-uploader", description=__doc__)

    def windows_iwa_client() -> ArcGISClient:
        nonlocal iwa_client
        if iwa_client is None:
            iwa_client = ArcGISClient(
                replace(
                    settings,
                    arcgis_auth_mode="iwa",
                    username="",
                    password="",
                )
            )
        return iwa_client

    @app.get("/", include_in_schema=False)
    @app.get("/example1", include_in_schema=False)
    def example1_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "example1.html")

    @app.get("/preview", include_in_schema=False)
    @app.get("/example2", include_in_schema=False)
    def example2_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "example2.html")

    @app.get("/example3", include_in_schema=False)
    def example3_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "example3.html")

    @app.get("/example4", include_in_schema=False)
    def example4_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "example4.html")

    @app.get("/api/info")
    def info() -> dict:
        return {
            "accepted_extensions": sorted(ACCEPTED_UPLOADS),
            "geometry_types": sorted(
                FAMILY_LABELS[t] for t in settings.layer_urls
            ),
            "project_id_field": settings.project_id_field,
            "project_id_pattern": settings.project_id_pattern,
            "max_upload_mb": settings.max_upload_mb,
            "dry_run": settings.dry_run,
            "basemap_url": settings.basemap_url,
            "username_field": settings.username_field,
            "duplicate_detection": settings.duplicate_detection,
            "duplicate_id_field": settings.duplicate_id_field
            or settings.project_id_field,
            "duplicate_tolerance_m": settings.duplicate_tolerance_m,
            "duplicate_compare_layer_count": len(settings.duplicate_compare_layers),
            "arcgis_auth_mode": settings.arcgis_auth_mode,
            "oauth_client_id": settings.oauth_client_id,
        }

    @app.get("/api/oauth-info")
    def oauth_info() -> dict:
        if not settings.portal_url:
            raise HTTPException(422, "PORTAL_URL is required for browser SSO.")
        authorize_url = urljoin(
            settings.portal_url + "/", "sharing/rest/oauth2/authorize"
        )
        params = {
            "client_id": settings.oauth_client_id,
            "response_type": "code",
            "redirect_uri": OAUTH_REDIRECT_URI,
            "expiration": "60",
        }
        return {
            "authorize_url": f"{authorize_url}?{urlencode(params)}",
            "client_id": settings.oauth_client_id,
            "redirect_uri": OAUTH_REDIRECT_URI,
        }

    @app.post("/api/oauth-session")
    def oauth_session(code: str = Form(...)) -> dict:
        if not settings.portal_url:
            raise HTTPException(422, "PORTAL_URL is required for browser SSO.")
        token_body = _exchange_oauth_code(settings, code)
        token = str(token_body["token"])
        expires = _oauth_token_expires(token_body)
        session_id = secrets.token_urlsafe(24)
        oauth_sessions[session_id] = {"token": token, "expires": expires}
        return {
            "session_id": session_id,
            "expires": int(expires * 1000),
            "username": _oauth_username(settings, token),
        }

    @app.post("/api/preview")
    def preview(
        request: Request,
        file: UploadFile = File(...),
        username: str | None = Form(None),
    ) -> dict:
        """Parse an upload and report what /api/upload would do with it,
        without contacting ArcGIS. Nothing is stored server-side: the client
        re-sends the same file to /api/upload once the user confirms."""
        with tempfile.TemporaryDirectory(prefix="arcgis-uploader-") as tmp:
            workdir = Path(tmp)
            upload_path = _receive_upload(file, workdir, settings.max_upload_mb)
            try:
                result = build_preview(
                    upload_path, workdir, settings.default_source_epsg
                )
            except IngestError as exc:
                raise HTTPException(422, str(exc)) from exc
        # So the page can show the attributes that WILL be added, not just
        # the ones removed.
        resolved_username = _resolve_username(request, username, settings)
        result["uploaded_by"] = resolved_username
        result["username_attribute_value"] = _username_attribute_value(
            resolved_username
        )
        return result

    @app.post("/api/upload")
    def upload(
        request: Request,
        file: UploadFile = File(...),
        project_id: str = Form(...),
        username: str | None = Form(None),
    ) -> dict:
        return _upload_request(request, file, project_id, username, settings, client)

    @app.post("/api/upload-iwa")
    def upload_iwa(
        request: Request,
        file: UploadFile = File(...),
        project_id: str = Form(...),
    ) -> dict:
        """Example 4 upload: force ArcGIS token generation with Windows IWA.

        The endpoint intentionally has no `username` form field. Upload
        attribution is informational and still resolves from the trusted proxy
        header (or "unknown" in bare local development), while the ArcGIS edit
        token comes from the Windows account running the server process.
        """
        return _upload_request(
            request,
            file,
            project_id,
            None,
            replace(settings, arcgis_auth_mode="iwa", username="", password=""),
            client if settings.dry_run else windows_iwa_client(),
        )

    @app.post("/api/upload-browser-sso")
    def upload_browser_sso(
        request: Request,
        file: UploadFile = File(...),
        project_id: str = Form(...),
        oauth_session: str = Form(...),
    ) -> dict:
        """Example 4 upload: use the browser user's OAuth/SSO token.

        The browser user signs in to Portal, pastes the approval code, and the
        backend exchanges that code for an ArcGIS token. This avoids the
        server-process/double-hop problem: ArcGIS sees the browser SSO user,
        not the uvicorn service account.
        """
        oauth_client = (
            client
            if settings.dry_run and oauth_session == "dry-run"
            else _client_for_oauth_session(settings, oauth_sessions, oauth_session)
        )
        return _upload_request(
            request,
            file,
            project_id,
            None,
            replace(settings, arcgis_auth_mode="password", username="", password=""),
            oauth_client,
        )

    return app


def _upload_request(
    request: Request,
    file: UploadFile,
    project_id: str,
    form_username: str | None,
    settings: Settings,
    client: ArcGISClient,
) -> dict:
    project_id = project_id.strip()
    if not re.fullmatch(settings.project_id_pattern, project_id):
        raise HTTPException(
            422, f"project_id must match {settings.project_id_pattern}"
        )
    username = _resolve_username(request, form_username, settings)
    with tempfile.TemporaryDirectory(prefix="arcgis-uploader-") as tmp:
        workdir = Path(tmp)
        upload_path = _receive_upload(file, workdir, settings.max_upload_mb)
        try:
            buckets = collect_geometries(
                upload_path, workdir, settings.default_source_epsg
            )
        except IngestError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            result = _append(buckets, project_id, username, settings, client)
        except DuplicateAppendError as exc:
            logger.info(
                "duplicate append refused: user=%s project=%s file=%s: %s",
                username, project_id, file.filename, exc,
            )
            raise HTTPException(409, str(exc)) from exc
        except ArcGISError as exc:
            logger.warning(
                "append failed: user=%s project=%s file=%s: %s",
                username, project_id, file.filename, exc,
            )
            raise HTTPException(502, str(exc)) from exc
    logger.info(
        "appended: user=%s project=%s file=%s appended=%s skipped=%s dry_run=%s",
        username, project_id, file.filename,
        result["features_appended"],
        result["features_skipped_no_target_layer"],
        settings.dry_run,
    )
    return result


def _exchange_oauth_code(settings: Settings, pasted_code: str) -> dict:
    code = _extract_oauth_code(pasted_code)
    if not code:
        raise HTTPException(422, "Paste the ArcGIS OAuth approval code or URL.")
    token_url = urljoin(settings.portal_url + "/", "sharing/rest/oauth2/token")
    try:
        response = requests.post(
            token_url,
            data={
                "client_id": settings.oauth_client_id,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": OAUTH_REDIRECT_URI,
                "f": "json",
            },
            timeout=60,
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(502, f"OAuth code exchange failed: {exc}") from exc

    if isinstance(body, dict) and "error" in body:
        error = body.get("error") or {}
        details = " ".join(str(item) for item in (error.get("details") or []))
        message = f"{error.get('message', 'ArcGIS OAuth error')} {details}".strip()
        raise HTTPException(502, message)

    token = body.get("access_token") or body.get("token")
    if not token:
        raise HTTPException(502, "OAuth code exchange succeeded but returned no token.")
    normalized = dict(body)
    normalized["token"] = str(token)
    return normalized


def _extract_oauth_code(pasted: str) -> str:
    pasted = pasted.strip()
    if not pasted:
        return ""

    parsed = urlparse(pasted)
    query_code = parse_qs(parsed.query).get("code")
    if query_code:
        return query_code[0].strip()
    fragment_code = parse_qs(parsed.fragment).get("code")
    if fragment_code:
        return fragment_code[0].strip()

    match = re.search(r"(?:[?&]code=|code[:=]\s*)([A-Za-z0-9._~-]+)", pasted)
    if match:
        return match.group(1).strip()
    return pasted


def _oauth_token_expires(body: dict) -> float:
    if body.get("expires") is not None:
        try:
            return int(body["expires"]) / 1000.0
        except (TypeError, ValueError):
            pass
    if body.get("expires_in") is not None:
        try:
            return time.time() + int(body["expires_in"])
        except (TypeError, ValueError):
            pass
    return time.time() + 60 * 60


def _oauth_username(settings: Settings, token: str) -> str:
    try:
        response = requests.get(
            urljoin(settings.portal_url + "/", "sharing/rest/community/self"),
            params={"f": "json", "token": token},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError):
        return ""
    if isinstance(body, dict) and not body.get("error"):
        return str(body.get("username") or "")
    return ""


def _client_for_oauth_session(
    settings: Settings,
    oauth_sessions: dict[str, dict],
    session_id: str,
) -> ArcGISClient:
    session = oauth_sessions.get(session_id)
    if not session:
        raise HTTPException(401, "Sign in to ArcGIS first; the browser SSO session is missing.")
    expires = float(session.get("expires") or 0)
    if time.time() >= expires - 60:
        oauth_sessions.pop(session_id, None)
        raise HTTPException(401, "Sign in to ArcGIS again; the browser SSO token expired.")

    client = ArcGISClient(
        replace(settings, arcgis_auth_mode="password", username="", password="")
    )
    client._token = str(session["token"])
    client._token_expires = expires
    return client


def _resolve_username(
    request: Request, form_value: str | None, settings: Settings
) -> str:
    """Whose name goes on the features, in order of preference:

    1. the `username` form field — for third-party apps calling this API
       that already authenticated their user. Set
       ALLOW_CLIENT_USERNAME=false to ignore it (e.g. when browsers can
       reach the API directly and callers cannot be trusted).
    2. the USERNAME_HEADER set by the SSO/reverse proxy in front of this
       app; browser code cannot supply the OS username itself.
    3. "unknown" — bare development runs.
    """
    if settings.allow_client_username and form_value and form_value.strip():
        return form_value.strip()[:128]
    header_value = (request.headers.get(settings.username_header) or "").strip()
    return header_value[:128] or "unknown"


def _username_attribute_value(username: str) -> str:
    """Value written to USERNAME_FIELD on the target layer."""
    max_username_len = (
        USERNAME_ATTRIBUTE_MAX_LEN
        - len(USERNAME_ATTRIBUTE_PREFIX)
        - len(USERNAME_ATTRIBUTE_SUFFIX)
    )
    return (
        USERNAME_ATTRIBUTE_PREFIX
        + username[:max_username_len]
        + USERNAME_ATTRIBUTE_SUFFIX
    )


def _receive_upload(file: UploadFile, workdir: Path, max_mb: int) -> Path:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ACCEPTED_UPLOADS:
        raise HTTPException(
            415,
            f"Unsupported file type '{suffix or file.filename}'. "
            f"Accepted: {', '.join(sorted(ACCEPTED_UPLOADS))}",
        )
    upload_path = workdir / f"upload{suffix}"
    _save_upload(file, upload_path, max_mb)
    return upload_path


def _save_upload(file: UploadFile, dest: Path, max_mb: int) -> None:
    limit = max_mb * 1024 * 1024
    written = 0
    with dest.open("wb") as out:
        while chunk := file.file.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                raise HTTPException(413, f"Upload exceeds the {max_mb} MB limit.")
            out.write(chunk)
    if written == 0:
        raise HTTPException(422, "The uploaded file is empty.")


def _append(
    buckets: GeometryBuckets,
    project_id: str,
    username: str,
    settings: Settings,
    client: ArcGISClient,
) -> dict:
    appended: dict[str, int] = {}
    skipped_no_target: dict[str, int] = {}
    sample_feature: dict | None = None
    username_attribute = _username_attribute_value(username)

    for esri_type, series_list in buckets.by_family.items():
        label = FAMILY_LABELS[esri_type]
        layer_url = settings.layer_urls.get(esri_type)

        if settings.dry_run:
            # Exercise the full conversion without contacting ArcGIS, so the
            # example runs end-to-end with no Enterprise instance.
            attributes = {
                settings.project_id_field: project_id,
                settings.username_field: username_attribute,
            }
            features = _build_features(series_list, esri_type, 4326, attributes)
            appended[label] = len(features)
            sample_feature = sample_feature or features[0]
            continue

        if not layer_url:
            skipped_no_target[label] = sum(len(s) for s in series_list)
            continue

        duplicate_id_field = settings.duplicate_id_field or settings.project_id_field
        required_fields = [settings.project_id_field, settings.username_field]
        if settings.duplicate_detection and duplicate_id_field not in required_fields:
            required_fields.append(duplicate_id_field)
        resolved = client.validate_layer(layer_url, esri_type, required_fields)
        attributes = {
            resolved[settings.project_id_field]: project_id,
            resolved[settings.username_field]: username_attribute,
        }
        if settings.duplicate_detection and duplicate_id_field != settings.project_id_field:
            attributes[resolved[duplicate_id_field]] = project_id
        wkid = client.layer_wkid(layer_url)
        features = _build_features(
            series_list,
            esri_type,
            wkid,
            attributes,
            has_z=client.layer_has_z(layer_url),
        )
        if settings.duplicate_detection:
            existing_geometries = client.duplicate_geometries(
                layer_url, resolved[duplicate_id_field], project_id, wkid
            )
            for compare_layer in settings.duplicate_compare_layers:
                existing_geometries.extend(
                    client.duplicate_geometries(
                        compare_layer.url,
                        compare_layer.id_field,
                        project_id,
                        wkid,
                    )
                )
            duplicate_count = count_duplicate_shapes(
                features,
                existing_geometries,
                wkid,
                settings.duplicate_tolerance_m,
            )
            if duplicate_count:
                checked_layers = 1 + len(settings.duplicate_compare_layers)
                checked = (
                    "the target layer"
                    if checked_layers == 1
                    else f"{checked_layers} checked layer(s)"
                )
                raise DuplicateAppendError(
                    f"{duplicate_count} {label} feature(s) already exist with "
                    f"id '{project_id}' and matching "
                    f"Shape within {settings.duplicate_tolerance_m:g} m; "
                    f"append refused after checking {checked}."
                )
        appended[label] = client.add_features(layer_url, features)

    result = {
        "project_id": project_id,
        "uploaded_by": username,
        "username_attribute_value": username_attribute,
        "layers_read": buckets.layers,
        "features_appended": appended,
        "features_skipped_no_target_layer": skipped_no_target,
        "features_skipped_invalid": buckets.skipped,
        "dry_run": settings.dry_run,
    }
    if sample_feature:
        result["sample_feature"] = sample_feature
    return result


def _build_features(
    series_list, esri_type: str, wkid: int, attributes: dict, *, has_z: bool = False
) -> list[dict]:
    target_crs = _crs_from_wkid(wkid)
    return [
        {
            "geometry": to_esri_geometry(esri_type, geom, wkid, has_z=has_z),
            "attributes": attributes,
        }
        for series in series_list
        for geom in series.to_crs(target_crs)
    ]


def _crs_from_wkid(wkid: int) -> CRS:
    try:
        return CRS.from_epsg(wkid)
    except CRSError:
        # Some layers report an Esri well-known id (e.g. 102100) with no
        # EPSG equivalent listed.
        return CRS.from_authority("ESRI", wkid)


app = create_app()
