from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)

from app.config import ESRI_POINT, ESRI_POLYGON, ESRI_POLYLINE
from app.esri import explode_to_families, to_esri_geometry


def signed_area(ring: list[list[float]]) -> float:
    """Shoelace formula: positive = counter-clockwise."""
    return sum(
        (x2 - x1) * (y2 + y1) for (x1, y1), (x2, y2) in zip(ring, ring[1:])
    ) / -2.0


def test_point():
    [(family, geom)] = explode_to_families(Point(1, 2))
    assert family == ESRI_POINT
    assert to_esri_geometry(family, geom, 4326) == {
        "x": 1.0,
        "y": 2.0,
        "spatialReference": {"wkid": 4326},
    }


def test_multipoint_explodes_to_single_points():
    parts = list(explode_to_families(MultiPoint([(0, 0), (1, 1), (2, 2)])))
    assert [family for family, _ in parts] == [ESRI_POINT] * 3
    assert [g.x for _, g in parts] == [0, 1, 2]


def test_linestring_to_paths():
    [(family, geom)] = explode_to_families(LineString([(0, 0), (5, 5)]))
    assert family == ESRI_POLYLINE
    esri = to_esri_geometry(family, geom, 3857)
    assert esri["paths"] == [[[0.0, 0.0], [5.0, 5.0]]]


def test_point_z_is_included_when_target_layer_has_z():
    [(family, geom)] = explode_to_families(Point(1, 2, 3))
    assert to_esri_geometry(family, geom, 4326, has_z=True) == {
        "x": 1.0,
        "y": 2.0,
        "z": 3.0,
        "spatialReference": {"wkid": 4326},
    }


def test_default_z_is_added_to_2d_vertices_when_target_layer_has_z():
    [(family, geom)] = explode_to_families(LineString([(0, 0), (5, 5)]))
    esri = to_esri_geometry(family, geom, 3857, has_z=True)
    assert esri["hasZ"] is True
    assert esri["paths"] == [[[0.0, 0.0, 0.0], [5.0, 5.0, 0.0]]]


def test_multilinestring_stays_one_feature_with_two_paths():
    geom = MultiLineString([[(0, 0), (1, 1)], [(2, 2), (3, 3)]])
    [(family, kept)] = explode_to_families(geom)
    assert family == ESRI_POLYLINE
    assert len(to_esri_geometry(family, kept, 4326)["paths"]) == 2


def test_polygon_ring_winding_follows_esri_convention():
    # Counter-clockwise exterior with a clockwise hole (GeoJSON convention) —
    # both must be flipped for Esri.
    shell = [(0, 0), (10, 0), (10, 10), (0, 10)]
    hole = [(2, 2), (2, 4), (4, 4), (4, 2)]
    [(family, geom)] = explode_to_families(Polygon(shell, [hole]))
    rings = to_esri_geometry(family, geom, 4326)["rings"]
    assert family == ESRI_POLYGON
    assert len(rings) == 2
    assert signed_area(rings[0]) < 0  # exterior clockwise
    assert signed_area(rings[1]) > 0  # hole counter-clockwise


def test_polygon_rings_include_default_z_when_target_layer_has_z():
    [(family, geom)] = explode_to_families(Polygon([(0, 0), (1, 0), (1, 1)]))
    esri = to_esri_geometry(family, geom, 4326, has_z=True)
    assert esri["hasZ"] is True
    rings = esri["rings"]
    assert all(len(vertex) == 3 for vertex in rings[0])
    assert all(vertex[2] == 0.0 for vertex in rings[0])


def test_multipolygon_concatenates_rings():
    geom = MultiPolygon(
        [
            Polygon([(0, 0), (1, 0), (1, 1)]),
            Polygon([(5, 5), (6, 5), (6, 6)]),
        ]
    )
    [(family, kept)] = explode_to_families(geom)
    assert len(to_esri_geometry(family, kept, 4326)["rings"]) == 2


def test_geometry_collection_is_flattened():
    geom = GeometryCollection(
        [Point(0, 0), LineString([(0, 0), (1, 1)]), MultiPoint([(2, 2), (3, 3)])]
    )
    families = [family for family, _ in explode_to_families(geom)]
    assert families == [ESRI_POINT, ESRI_POLYLINE, ESRI_POINT, ESRI_POINT]
