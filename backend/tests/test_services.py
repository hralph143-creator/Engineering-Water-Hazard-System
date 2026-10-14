from pathlib import Path

import pytest

from backend.app.services.raster_service import RasterValidationError, inspect_geotiff
from tests.make_demo_data import generate_demo


def test_inspect_valid_two_band_geotiff(tmp_path):
    generate_demo(tmp_path)
    image = next((tmp_path / "v1.1/data/flood_events/HandLabeled/S1Hand").glob("*.tif"))
    info = inspect_geotiff(image)
    assert info["bands"] == 2
    assert info["width"] == info["height"] == 64
    assert info["crs"] == "EPSG:4326"


def test_reject_non_geotiff(tmp_path):
    path = tmp_path / "invalid.txt"
    path.write_text("not a raster", encoding="utf-8")
    with pytest.raises(RasterValidationError, match="GeoTIFF"):
        inspect_geotiff(path)

