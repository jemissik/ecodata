import shutil
from pathlib import Path
from sys import platform

import pytest
import requests

import geopandas as gpd
import panel as pn
import xarray as xr
from ecodata.tests.conftest import test_data_dir


@pytest.mark.skipif(platform == "linux" or platform == "linux2", reason="needs update")
def test_server_has_every_app(serve_apps, port, apps):
    for app in apps:
        r = requests.get(f"http://localhost:{port}/{app}")
        assert 200 <= int(r.status_code) <= 299


# TODO you could add tests using playwright to doing ui browser testing?


def test_track_explorer_load_data(install_test_data, track_explorer):
    track_explorer.tracksfile.value = str(test_data_dir / "public_caribou_tracks.csv")
    track_explorer.load_data()

    assert track_explorer.tracks is not None
    assert isinstance(track_explorer.tracks, gpd.GeoDataFrame)

    # check plot was created
    assert track_explorer.plot_pane.object is not None


def test_gridded_data_explorer_load_data(install_test_data, gridded_data_explorer):
    gridded_data_explorer.filein.value = str(test_data_dir / "NASA_public_caribou.nc")
    gridded_data_explorer.load_data()

    assert gridded_data_explorer.ds_raw is not None
    assert gridded_data_explorer.ds is not None
    assert isinstance(gridded_data_explorer.ds_raw, xr.Dataset)
    assert isinstance(gridded_data_explorer.ds, xr.Dataset)

    # check plot was created
    # assert track_explorer.plot_pane.object is not None


def test_subsetter_load_data(install_test_data, subsetter, tmp_path):
    subsetter.input_file.value = str(test_data_dir / "public_caribou_roads")
    subsetter.output_file.value = str(tmp_path / "output_file")
    subsetter.option_picker.value = "bounding_geom"
    subsetter.bounding_geom_file.value = str(test_data_dir / "public_caribou_tracks_extent.geojson")
    subsetter.boundary_type_geom.value = "rectangular"

    subsetter.create_subset()

    assert subsetter.view is not None
    assert subsetter.view[subsetter.view_objects["plot"]].object != "## Create a subset!"
    assert isinstance(subsetter.view[subsetter.view_objects["plot"]], pn.pane.Matplotlib)

    # check plot was created
    # assert track_explorer.plot_pane.object is not None
