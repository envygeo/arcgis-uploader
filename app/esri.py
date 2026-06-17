"""Convert shapely geometries to Esri JSON.

ArcGIS feature layers store exactly one of three geometry families: point,
polyline, polygon. Multi-line and multi-polygon map natively onto Esri
paths/rings, but a point layer only accepts single points, so multipoints
are exploded into one feature per point.
"""
from __future__ import annotations

import math
from typing import Iterator

from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from .config import ESRI_POINT, ESRI_POLYGON, ESRI_POLYLINE

_FAMILY = {
    "Point": ESRI_POINT,
    "MultiPoint": ESRI_POINT,
    "LineString": ESRI_POLYLINE,
    "MultiLineString": ESRI_POLYLINE,
    "Polygon": ESRI_POLYGON,
    "MultiPolygon": ESRI_POLYGON,
}


def explode_to_families(geom: BaseGeometry) -> Iterator[tuple[str, BaseGeometry]]:
    """Yield (Esri geometry type, geometry) pairs for one source geometry.

    Yields nothing for types ArcGIS cannot store (the caller counts those
    as skipped).
    """
    gtype = geom.geom_type
    if gtype == "GeometryCollection":
        for part in geom.geoms:
            yield from explode_to_families(part)
    elif gtype == "MultiPoint":
        for part in geom.geoms:
            yield ESRI_POINT, part
    elif gtype in _FAMILY:
        yield _FAMILY[gtype], geom


def to_esri_geometry(
    esri_type: str,
    geom: BaseGeometry,
    wkid: int,
    *,
    has_z: bool = False,
    default_z: float = 0.0,
) -> dict:
    sr = {"spatialReference": {"wkid": wkid}}
    if esri_type == ESRI_POINT:
        point = {"x": geom.x, "y": geom.y, **sr}
        if has_z:
            point["z"] = _z_value(geom.coords[0], default_z)
        return point
    if esri_type == ESRI_POLYLINE:
        lines = geom.geoms if geom.geom_type == "MultiLineString" else [geom]
        polyline = {
            "paths": [_coords(line, has_z=has_z, default_z=default_z) for line in lines],
            **sr,
        }
        if has_z:
            polyline["hasZ"] = True
        return polyline
    if esri_type == ESRI_POLYGON:
        polygons = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        rings = []
        for polygon in polygons:
            # Esri winding order: exterior rings clockwise, holes counter-clockwise
            # (the opposite of GeoJSON).
            polygon = orient(polygon, sign=-1.0)
            rings.append(_coords(polygon.exterior, has_z=has_z, default_z=default_z))
            rings.extend(
                _coords(hole, has_z=has_z, default_z=default_z)
                for hole in polygon.interiors
            )
        polygon = {"rings": rings, **sr}
        if has_z:
            polygon["hasZ"] = True
        return polygon
    raise ValueError(f"unsupported Esri geometry type: {esri_type}")


def _coords(line, *, has_z: bool, default_z: float) -> list[list[float]]:
    coords = []
    for coord in line.coords:
        point = [float(coord[0]), float(coord[1])]
        if has_z:
            point.append(_z_value(coord, default_z))
        coords.append(point)
    return coords


def _z_value(coord, default_z: float) -> float:
    if len(coord) < 3:
        return float(default_z)
    z = float(coord[2])
    return z if math.isfinite(z) else float(default_z)
