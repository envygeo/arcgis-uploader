"""Duplicate detection for ArcGIS feature appends.

Duplicates are defined as:

* the same configured id field value; and
* a matching Shape, compared in metres with a small tolerance.
"""
from __future__ import annotations

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError
from shapely.geometry import LineString, LinearRing, MultiLineString, MultiPolygon
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union


def count_duplicate_shapes(
    outgoing_features: list[dict],
    existing_geometries: list[dict],
    wkid: int,
    tolerance_m: float,
) -> int:
    """Count outgoing features whose geometry matches an existing geometry.

    The caller is expected to have already filtered the existing features by
    id field. Geometry comparison is pairwise and stops at the first existing
    match for each outgoing feature.
    """
    if not outgoing_features or not existing_geometries:
        return 0

    source_crs = _crs_from_wkid(wkid)
    existing = [esri_geometry_to_shapely(g) for g in existing_geometries]
    duplicates = 0

    for feature in outgoing_features:
        outgoing = esri_geometry_to_shapely(feature["geometry"])
        for candidate in existing:
            if shapes_match_within(outgoing, candidate, source_crs, tolerance_m):
                duplicates += 1
                break
    return duplicates


def shapes_match_within(
    left: BaseGeometry,
    right: BaseGeometry,
    source_crs: CRS,
    tolerance_m: float,
) -> bool:
    """Return True when two shapes are the same within ``tolerance_m`` metres."""
    if left.is_empty or right.is_empty:
        return False
    left_m, right_m = _metric_pair(left, right, source_crs)
    if left_m.equals(right_m):
        return True
    return left_m.hausdorff_distance(right_m) <= tolerance_m


def esri_geometry_to_shapely(geometry: dict) -> BaseGeometry:
    """Convert the Esri JSON emitted/returned by this app into Shapely."""
    if "x" in geometry and "y" in geometry:
        return Point(float(geometry["x"]), float(geometry["y"]))
    if "paths" in geometry:
        paths = [LineString(_xy(path)) for path in geometry.get("paths") or []]
        return paths[0] if len(paths) == 1 else MultiLineString(paths)
    if "rings" in geometry:
        return _polygon_from_rings(geometry.get("rings") or [])
    raise ValueError("unsupported Esri geometry")


def _polygon_from_rings(rings: list[list[list[float]]]) -> BaseGeometry:
    shells: list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]] = []
    loose_holes: list[list[tuple[float, float]]] = []

    for raw_ring in rings:
        ring = _xy(raw_ring)
        if len(ring) < 4:
            continue
        linear = LinearRing(ring)
        # Esri convention: exterior rings are clockwise, holes are
        # counter-clockwise. The uploader emits that convention and ArcGIS
        # normally returns it.
        if linear.is_ccw:
            loose_holes.append(ring)
        else:
            shells.append((ring, []))

    if not shells and loose_holes:
        first, *rest = loose_holes
        shells.append((first, rest))
        loose_holes = []

    # Attach holes to the shell that contains them. If containment cannot be
    # determined, keep the hole with the first shell rather than dropping it.
    for hole in loose_holes:
        hole_point = Point(hole[0])
        for shell, holes in shells:
            if Polygon(shell).contains(hole_point):
                holes.append(hole)
                break
        else:
            if shells:
                shells[0][1].append(hole)

    polygons = [Polygon(shell, holes) for shell, holes in shells]
    if not polygons:
        return Polygon()
    return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)


def _xy(coords) -> list[tuple[float, float]]:
    return [(float(coord[0]), float(coord[1])) for coord in coords]


def _metric_pair(
    left: BaseGeometry, right: BaseGeometry, source_crs: CRS
) -> tuple[BaseGeometry, BaseGeometry]:
    """Project a geometry pair to a local metre CRS for distance checks."""
    to_wgs84 = Transformer.from_crs(source_crs, 4326, always_xy=True)
    left_wgs84 = transform(to_wgs84.transform, left)
    right_wgs84 = transform(to_wgs84.transform, right)
    center = unary_union([left_wgs84, right_wgs84]).centroid

    local_m = CRS.from_proj4(
        "+proj=aeqd "
        f"+lat_0={center.y} +lon_0={center.x} "
        "+datum=WGS84 +units=m +no_defs"
    )
    to_local_m = Transformer.from_crs(4326, local_m, always_xy=True)
    return (
        transform(to_local_m.transform, left_wgs84),
        transform(to_local_m.transform, right_wgs84),
    )


def _crs_from_wkid(wkid: int) -> CRS:
    try:
        return CRS.from_epsg(wkid)
    except CRSError:
        return CRS.from_authority("ESRI", wkid)
