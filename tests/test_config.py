from app.config import parse_duplicate_compare_layers


def test_parse_duplicate_compare_layers_json():
    layers = parse_duplicate_compare_layers(
        """[{"id_field":"attr_yesab_proj","url":"https://example.test/FeatureServer/3/"}]"""
    )

    assert len(layers) == 1
    assert layers[0].id_field == "attr_yesab_proj"
    assert layers[0].url == "https://example.test/FeatureServer/3"


def test_parse_duplicate_compare_layers_line_format():
    layers = parse_duplicate_compare_layers(
        """
        'attr_yesab_proj', https://example.test/FeatureServer/3
        'attr_yesab_proj', https://example.test/FeatureServer/4,
        """
    )

    assert [layer.id_field for layer in layers] == ["attr_yesab_proj", "attr_yesab_proj"]
    assert [layer.url for layer in layers] == [
        "https://example.test/FeatureServer/3",
        "https://example.test/FeatureServer/4",
    ]


def test_parse_duplicate_compare_layers_triple_quoted_line_format():
    layers = parse_duplicate_compare_layers(
        '''"""
        'attr_yesab_proj', https://example.test/FeatureServer/5
        """'''
    )

    assert len(layers) == 1
    assert layers[0].url == "https://example.test/FeatureServer/5"
