"""Build a side-effect-free preview of an upload: geometry for a web map,
a sample of the attribute table, and the same per-family feature counts the
real append would produce.

The attribute sample exists so the user can see what is about to be
REMOVED — the append pipeline strips every column.
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import mapping

from .config import FAMILY_LABELS
from .esri import explode_to_families
from .ingest import IngestError, read_layers, resolve_crs, spatial_sources

MAX_PREVIEW_FEATURES = 2000  # cap on what we send to the browser map
SAMPLE_ROWS = 5


def build_preview(
    upload_path: Path, workdir: Path, default_epsg: int | None
) -> dict:
    layers: list[dict] = []
    counts: dict[str, int] = {}
    skipped = 0
    map_features: list[dict] = []
    truncated = False

    for path in spatial_sources(upload_path, workdir):
        for layer_name, gdf in read_layers(path):
            label = f"{path.name}:{layer_name}"
            crs = resolve_crs(gdf, label, default_epsg)
            layers.append(_attribute_sample(label, gdf))

            geoms = gdf.geometry.dropna()
            geoms = geoms[~geoms.is_empty]
            skipped += len(gdf) - len(geoms)

            # Count exactly as the append would: multipoints explode into
            # individual point features, multi-line/-polygon stay whole.
            for geom in geoms:
                parts = list(explode_to_families(shapely.force_2d(geom)))
                if not parts:
                    skipped += 1
                    continue
                for family, _ in parts:
                    counts[FAMILY_LABELS[family]] = (
                        counts.get(FAMILY_LABELS[family], 0) + 1
                    )

            # Web maps speak WGS84.
            web = geoms.set_crs(crs, allow_override=True).to_crs(4326)
            for geom in web:
                if len(map_features) >= MAX_PREVIEW_FEATURES:
                    truncated = True
                    break
                map_features.append(
                    {
                        "type": "Feature",
                        "properties": {"layer": label},
                        "geometry": mapping(shapely.force_2d(geom)),
                    }
                )

    if not layers:
        raise IngestError("No spatial layers found in the upload.")
    if not counts:
        raise IngestError("The upload contains no usable geometries.")
    return {
        "layers": layers,
        "feature_counts": counts,
        "features_skipped_invalid": skipped,
        "geojson": {"type": "FeatureCollection", "features": map_features},
        "geojson_truncated": truncated,
    }


def _attribute_sample(label: str, gdf: gpd.GeoDataFrame) -> dict:
    columns = [c for c in gdf.columns if c != gdf.geometry.name]
    rows = [
        [_cell(value) for value in record]
        for record in gdf[columns]
        .head(SAMPLE_ROWS)
        .itertuples(index=False, name=None)
    ]
    return {
        "layer": label,
        "feature_count": len(gdf),
        "columns": columns,
        "rows": rows,
    }


def _cell(value):
    """Make one attribute value JSON-safe."""
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):  # numpy scalar -> plain Python scalar
        value = value.item()
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)  # dates, bytes, anything exotic
