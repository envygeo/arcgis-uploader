"""Minimal ArcGIS Enterprise REST client: generateToken + layer info + addFeatures.

Deliberately plain REST rather than the `arcgis` Python package, so the
recipe is portable to any language. The full sequence is documented in the
README ("Porting the whole pipeline").
"""
from __future__ import annotations

import json
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
        self._token: str | None = None
        self._token_expires = 0.0  # epoch seconds
        self._layer_info: dict[str, dict] = {}

    # -- auth -----------------------------------------------------------------
    def token(self) -> str | None:
        s = self.settings
        if not (s.token_url and s.username):
            return None  # anonymous; the layer must allow public editing
        if self._token and time.time() < self._token_expires - 60:
            return self._token
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
            {},
        )
        field_type = field.get("type")
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
                return f"{field_name} = {value}"
        escaped = value.replace("'", "''")
        return f"{field_name} = '{escaped}'"

    # -- plumbing -----------------------------------------------------------------
    def _post(self, url: str, data: dict, with_token: bool = True) -> dict:
        if with_token and (token := self.token()):
            data = {**data, "token": token}
        try:
            response = self.session.post(url, data=data, timeout=120)
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ArcGISError(f"Request to {url} failed: {exc}") from exc
        # ArcGIS reports most errors inside an HTTP 200 body.
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            details = " ".join(err.get("details") or [])
            raise ArcGISError(
                f"{url}: {err.get('message', 'ArcGIS error')} {details}".strip()
            )
        return body
