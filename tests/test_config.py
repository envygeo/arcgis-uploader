import pytest

from app.config import normalize_arcgis_auth_mode, parse_duplicate_compare_layers


def test_parse_duplicate_compare_layers_json():
    layers = parse_duplicate_compare_layers(
        """[{"id_field":"registry_project_id","url":"https://example.test/FeatureServer/3/"}]"""
    )

    assert len(layers) == 1
    assert layers[0].id_field == "registry_project_id"
    assert layers[0].url == "https://example.test/FeatureServer/3"


def test_parse_duplicate_compare_layers_line_format():
    layers = parse_duplicate_compare_layers(
        """
        'registry_project_id', https://example.test/FeatureServer/3
        'registry_project_id', https://example.test/FeatureServer/4,
        """
    )

    assert [layer.id_field for layer in layers] == ["registry_project_id", "registry_project_id"]
    assert [layer.url for layer in layers] == [
        "https://example.test/FeatureServer/3",
        "https://example.test/FeatureServer/4",
    ]


def test_parse_duplicate_compare_layers_triple_quoted_line_format():
    layers = parse_duplicate_compare_layers(
        '''"""
        'registry_project_id', https://example.test/FeatureServer/5
        """'''
    )

    assert len(layers) == 1
    assert layers[0].url == "https://example.test/FeatureServer/5"


def test_normalize_arcgis_auth_mode_aliases():
    assert normalize_arcgis_auth_mode("password") == "password"
    assert normalize_arcgis_auth_mode("windows") == "iwa"
    assert normalize_arcgis_auth_mode("SSPI") == "iwa"
    assert normalize_arcgis_auth_mode("none") == "anonymous"


def test_normalize_arcgis_auth_mode_rejects_unknown():
    with pytest.raises(ValueError, match="ARCGIS_AUTH_MODE"):
        normalize_arcgis_auth_mode("kerberos-only")
