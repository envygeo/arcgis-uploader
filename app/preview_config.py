"""Resolve the map configuration shared by the browser and debug-info."""
from copy import deepcopy
import json
from pathlib import Path

from .config import Settings


PREVIEW_MAP_DEFAULTS = json.loads(
    Path(__file__).with_name("preview-map.json").read_text(encoding="utf-8")
)


def preview_map_config(settings: Settings) -> dict:
    config = deepcopy(PREVIEW_MAP_DEFAULTS)
    if settings.basemap_url:
        config["basemap"].update(
            url=settings.basemap_url,
            type="xyz_tiles",
            source="BASEMAP_URL override",
            attribution=settings.basemap_attribution,
            attribution_source=(
                "BASEMAP_ATTRIBUTION override"
                if settings.basemap_attribution else "not supplied"
            ),
        )
    return config
