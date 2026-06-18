"""Minimal ArcGIS Enterprise REST client: generateToken + layer info + addFeatures.

Deliberately plain REST rather than the `arcgis` Python package, so the
recipe is portable to any language. The full sequence is documented in the
README ("Porting the whole pipeline").
"""
from __future__ import annotations

import getpass
import json
import os
import subprocess
import time

import requests

from .config import Settings

CHUNK_SIZE = 500  # features per addFeatures request
TOKEN_LIFETIME_MIN = 60


class ArcGISError(RuntimeError):
    """ArcGIS request failed; message is safe to show to the user."""


class ArcGISClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = requests.Session()
        if self.settings.arcgis_auth_mode == "iwa":
            self._add_iwa_auth()
        self._token: str | None = None
        self._token_expires = 0.0  # epoch seconds
        self._layer_info: dict[str, dict] = {}

    # -- auth -----------------------------------------------------------------
    def token(self) -> str | None:
        s = self.settings
        if s.arcgis_auth_mode == "anonymous":
            return None  # anonymous; the layer must allow public editing
        if not s.token_url:
            return None
        if self._token and time.time() < self._token_expires - 60:
            return self._token

        if s.arcgis_auth_mode == "iwa":
            body = self._generate_iwa_token()
        else:
            if not s.username:
                return None
            body = self._post(
                s.token_url,
                {
                    "username": s.username,
                    "password": s.password,
                    "client": "referer",
                    "referer": s.portal_url or s.token_url,
                    "expiration": TOKEN_LIFETIME_MIN,
                    "f": "json",
                },
                with_token=False,
            )
        self._token = body["token"]
        self._token_expires = body["expires"] / 1000.0
        return self._token

    def _add_iwa_auth(self) -> None:
        if os.name != "nt":
            raise ArcGISError(
                "ARCGIS_AUTH_MODE=iwa requires Windows. Run the service under "
                "the Windows/domain account that should authenticate to ArcGIS."
            )
        try:
            from requests_negotiate_sspi import HttpNegotiateAuth
        except ImportError as exc:
            raise ArcGISError(
                "ARCGIS_AUTH_MODE=iwa requires requests-negotiate-sspi. "
                "Install it with `uv sync` or `pip install requests-negotiate-sspi`."
            ) from exc
        self.session.auth = HttpNegotiateAuth()

    def _generate_iwa_token(self) -> dict:
        """Generate a Portal token using the Windows process identity.

        This is the unattended-server version of scripts/arcgis_iwa_token_check.py:
        no ARCGIS_USERNAME or ARCGIS_PASSWORD values are read or sent. The
        service account running uvicorn/IIS performs the SSPI/IWA handshake.
        """
        s = self.settings
        params = {
            "client": "referer",
            "referer": s.portal_url or s.token_url,
            "expiration": str(TOKEN_LIFETIME_MIN),
            "f": "json",
        }
        attempts: list[str] = []

        # Esri's IWA sample uses GET with SSPI. POST is kept as a fallback
        # because ordinary username/password generateToken flows use POST.
        for method in ("GET", "POST"):
            body = self._request_token(
                method,
                s.token_url,
                params=params if method == "GET" else None,
                data=params if method == "POST" else None,
            )
            if isinstance(body, dict) and body.get("token"):
                return body
            attempts.append(f"{method}: {self._arcgis_error_summary(body)}")

        raise ArcGISError(
            "Could not generate an ArcGIS token with Windows IWA credentials. "
            f"Attempted Windows identity: {windows_identity()}. "
            "Confirm the service is running as a domain account with edit "
            "access and that the Portal generateToken endpoint accepts "
            "Negotiate/NTLM.\n"
            + "\n".join(f"  - {attempt}" for attempt in attempts)
        )

    def _request_token(
        self,
        method: str,
        url: str,
        *,
        params: dict | None,
        data: dict | None,
    ) -> dict | None:
        try:
            response = self.session.request(
                method,
                url,
                params=params,
                data=data,
                timeout=120,
                headers={"Referer": self.settings.portal_url or url},
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            return {"error": {"message": f"Request failed: {exc}"}}

    def _arcgis_error_summary(self, body) -> str:
        if isinstance(body, dict) and "error" in body:
            error = body.get("error") or {}
            if isinstance(error, dict):
                details = " ".join(str(item) for item in (error.get("details") or []))
                return f"{error.get('message', 'ArcGIS error')} {details}".strip()
            return str(error)
        if isinstance(body, dict):
            keys = ", ".join(sorted(str(key) for key in body.keys())[:8])
            return f"JSON object without token; keys: {keys}"
        return "No JSON token response"

    # -- layer metadata ---------------------------------------------------------
    def layer_info(self, layer_url: str) -> dict:
        if layer_url not in self._layer_info:
            self._layer_info[layer_url] = self._post(layer_url, {"f": "json"})
        return self._layer_info[layer_url]

    def layer_wkid(self, layer_url: str) -> int:
        sr = (self.layer_info(layer_url).get("extent") or {}).get(
            "spatialReference"
        ) or {}
        wkid = sr.get("latestWkid") or sr.get("wkid")
        if not wkid:
            raise ArcGISError(
                f"Could not determine the spatial reference of {layer_url}"
            )
        return int(wkid)

    def layer_has_z(self, layer_url: str) -> bool:
        return bool(self.layer_info(layer_url).get("hasZ"))

    def duplicate_geometries(
        self, layer_url: str, id_field: str, id_value: str, wkid: int
    ) -> list[dict]:
        """Return destination geometries with ``id_field == id_value``.

        The geometry comparison itself happens in app.duplicates; this method
        only performs the ArcGIS query-side filtering by id.
        """
        info = self.layer_info(layer_url)
        where = self._where_equals(info, id_field, id_value)
        page_size = int(info.get("maxRecordCount") or 2000)
        offset = 0
        geometries: list[dict] = []

        while True:
            body = self._post(
                f"{layer_url}/query",
                {
                    "where": where,
                    "outFields": id_field,
                    "returnGeometry": "true",
                    "outSR": str(wkid),
                    "resultOffset": str(offset),
                    "resultRecordCount": str(page_size),
                    "f": "json",
                },
            )
            features = body.get("features") or []
            geometries.extend(
                feature["geometry"] for feature in features if feature.get("geometry")
            )
            if not body.get("exceededTransferLimit") or not features:
                return geometries
            offset += len(features)

    def validate_layer(
        self, layer_url: str, esri_type: str, required_fields: list[str]
    ) -> dict[str, str]:
        """Check geometry type, editability, and that every required field
        exists.

        Returns a mapping of requested name -> the layer's exact casing,
        since addFeatures attribute keys must match the schema.
        """
        info = self.layer_info(layer_url)
        if info.get("geometryType") != esri_type:
            raise ArcGISError(
                f"{layer_url} stores {info.get('geometryType')}, not {esri_type}; "
                "check the TARGET_LAYER_* mapping."
            )
        capabilities = (info.get("capabilities") or "").split(",")
        if "Create" not in capabilities:
            raise ArcGISError(
                f"{layer_url} does not allow adding features "
                f"(capabilities: {info.get('capabilities')})."
            )
        by_lower = {
            layer_field["name"].lower(): layer_field["name"]
            for layer_field in info.get("fields") or []
        }
        resolved = {}
        for name in required_fields:
            exact = by_lower.get(name.lower())
            if exact is None:
                raise ArcGISError(f"{layer_url} has no '{name}' field.")
            resolved[name] = exact
        return resolved

    # -- editing ------------------------------------------------------------------
    def add_features(self, layer_url: str, features: list[dict]) -> int:
        added = 0
        for start in range(0, len(features), CHUNK_SIZE):
            chunk = features[start : start + CHUNK_SIZE]
            body = self._post(
                f"{layer_url}/addFeatures",
                {
                    "features": json.dumps(chunk),
                    "rollbackOnFailure": "true",
                    "f": "json",
                },
            )
            results = body.get("addResults") or []
            failures = [r for r in results if not r.get("success")]
            if failures:
                detail = (failures[0].get("error") or {}).get(
                    "description", "unknown error"
                )
                raise ArcGISError(
                    f"{len(failures)} feature(s) were rejected: {detail}"
                )
            added += len(results)
        return added

    def _where_equals(self, info: dict, field_name: str, value: str) -> str:
        field = next(
            (
                field
                for field in info.get("fields") or []
                if field.get("name", "").lower() == field_name.lower()
            ),
            None,
        )
        if field is None:
            raise ArcGISError(
                f"{info.get('name') or 'Layer'} has no '{field_name}' field."
            )
        field_type = field.get("type")
        exact_field_name = field.get("name")
        numeric_types = {
            "esriFieldTypeSmallInteger",
            "esriFieldTypeInteger",
            "esriFieldTypeSingle",
            "esriFieldTypeDouble",
            "esriFieldTypeOID",
        }
        if field_type in numeric_types:
            try:
                float(value)
            except ValueError:
                pass
            else:
                return f"{exact_field_name} = {value}"
        escaped = value.replace("'", "''")
        return f"{exact_field_name} = '{escaped}'"

    # -- plumbing -----------------------------------------------------------------
    def _post(self, url: str, data: dict, with_token: bool = True) -> dict:
        if with_token and (token := self.token()):
            data = {**data, "token": token}
        try:
            response = self.session.post(url, data=data, timeout=120)
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ArcGISError(
                self._with_auth_context(f"Request to {url} failed: {exc}")
            ) from exc
        # ArcGIS reports most errors inside an HTTP 200 body.
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            details = " ".join(err.get("details") or [])
            raise ArcGISError(
                self._with_auth_context(
                    f"{url}: {err.get('message', 'ArcGIS error')} {details}".strip()
                )
            )
        return body

    def _with_auth_context(self, message: str) -> str:
        if self.settings.arcgis_auth_mode != "iwa":
            return message
        return f"ArcGIS IWA attempted Windows identity {windows_identity()}: {message}"


def windows_identity() -> str:
    """Return the Windows account used for SSPI/IWA diagnostics."""
    candidates: list[str] = []
    for command in (["whoami"], ["whoami", "/upn"]):
        try:
            output = subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL)
        except Exception:
            continue
        output = output.strip()
        if output and output not in candidates:
            candidates.append(output)
    if candidates:
        return " / ".join(candidates)
    domain = os.environ.get("USERDOMAIN", "").strip()
    user = getpass.getuser()
    return f"{domain}\\{user}" if domain else user
