"""Read an uploaded file into geometry buckets, one per Esri geometry family.

GDAL (via pyogrio) does the format detection, so anything it can read works:
zipped shapefiles, GeoPackage, GeoJSON, KML, FlatGeobuf. Zips are extracted
and every spatial file inside is read, so a zip may carry several shapefiles
or a geopackage. Attributes are discarded here — only geometry survives.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import geopandas as gpd
import pyogrio
import shapely

from .esri import explode_to_families

# Some data sources (including ArcGIS exports copied manually) arrive as
# shapefiles without their companion .shx index. GDAL can rebuild that index
# from the .shp, but only when this driver option is enabled before reads.
pyogrio.set_gdal_config_options({"SHAPE_RESTORE_SHX": "YES"})

# Files handed to GDAL when unpacking a zip; bare uploads are read directly.
SPATIAL_EXTENSIONS = {".shp", ".gpkg", ".geojson", ".json", ".kml", ".fgb"}
ACCEPTED_UPLOADS = SPATIAL_EXTENSIONS | {".zip"}


class IngestError(ValueError):
    """Problem with the uploaded data; safe to show to the user."""


@dataclass
class GeometryBuckets:
    """Geometries grouped by Esri family.

    Groups are kept as GeoSeries so each remembers its source CRS;
    reprojection happens at append time, against the target layer's
    spatial reference.
    """

    by_family: dict[str, list[gpd.GeoSeries]] = field(default_factory=dict)
    layers: list[str] = field(default_factory=list)  # "file:layer" labels read
    read: int = 0     # source features that produced at least one geometry
    skipped: int = 0  # null, empty, or unsupported-type geometries


def collect_geometries(
    upload_path: Path, workdir: Path, default_epsg: int | None
) -> GeometryBuckets:
    buckets = GeometryBuckets()
    for path in spatial_sources(upload_path, workdir):
        for layer_name, gdf in read_layers(path):
            _bucket_frame(buckets, f"{path.name}:{layer_name}", gdf, default_epsg)
    if not buckets.layers:
        raise IngestError("No spatial layers found in the upload.")
    if buckets.read == 0:
        raise IngestError("The upload contains no usable geometries.")
    return buckets


def spatial_sources(upload_path: Path, workdir: Path) -> list[Path]:
    if upload_path.suffix.lower() != ".zip":
        return [upload_path]
    extract_dir = workdir / "unzipped"
    try:
        with zipfile.ZipFile(upload_path) as zf:
            zf.extractall(extract_dir)
    except zipfile.BadZipFile as exc:
        raise IngestError("The uploaded file is not a valid zip archive.") from exc
    paths = sorted(
        p for p in extract_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in SPATIAL_EXTENSIONS
    )
    if not paths:
        raise IngestError(
            "The zip contains no spatial files "
            f"({', '.join(sorted(SPATIAL_EXTENSIONS))})."
        )
    return paths


def read_layers(path: Path) -> Iterator[tuple[str, gpd.GeoDataFrame]]:
    try:
        layers = pyogrio.list_layers(path)
    except Exception as exc:
        raise IngestError(f"Could not read {path.name}: {exc}") from exc
    for name, geometry_type in layers:
        if geometry_type is None:  # non-spatial table, e.g. inside a geopackage
            continue
        yield str(name), gpd.read_file(path, layer=name)


def resolve_crs(gdf: gpd.GeoDataFrame, label: str, default_epsg: int | None):
    if gdf.crs is not None:
        return gdf.crs
    if default_epsg is None:
        raise IngestError(
            f"'{label}' has no coordinate system (e.g. a shapefile missing "
            "its .prj). Re-export it with a CRS, or set DEFAULT_SOURCE_EPSG."
        )
    return f"EPSG:{default_epsg}"


def _bucket_frame(
    buckets: GeometryBuckets,
    label: str,
    gdf: gpd.GeoDataFrame,
    default_epsg: int | None,
) -> None:
    crs = resolve_crs(gdf, label, default_epsg)
    buckets.layers.append(label)

    geoms = gdf.geometry.dropna()
    geoms = geoms[~geoms.is_empty]
    buckets.skipped += len(gdf) - len(geoms)

    parts: dict[str, list] = {}
    for geom in geoms:
        # Normalize to 2D while bucketing; append restores default Z values
        # when the target layer metadata requires them.
        exploded = list(explode_to_families(shapely.force_2d(geom)))
        if not exploded:
            buckets.skipped += 1
            continue
        buckets.read += 1
        for family, part in exploded:
            parts.setdefault(family, []).append(part)

    for family, geom_list in parts.items():
        buckets.by_family.setdefault(family, []).append(
            gpd.GeoSeries(geom_list, crs=crs)
        )
