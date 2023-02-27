import shutil
import time

import panel as pn
import pytest

import ecodata
from ecodata.app.apps import applications
from ecodata.app.apps.tracks_explorer_app import TracksExplorer
from ecodata.app.apps.gridded_data_explorer_app import GriddedDataExplorer
from ecodata.app.apps.subsetter_app import Subsetter


PORT = [6000]

test_data_dir = ecodata.datasets.dataset_utils._module_path / "test_datasets"
test_frames_dir = test_data_dir / "animation_test_frames"
test_frames_dir_weird = test_data_dir / "animation & test & frames"

test_output_dir = ecodata.datasets.dataset_utils._module_path / "test_datasets" / "output"
test_output_dir_weird = ecodata.datasets.dataset_utils._module_path / "test_datasets" / "output & weird"


@pytest.fixture
def port():
    PORT[0] += 1
    return PORT[0]


@pytest.fixture
def apps():
    return applications


@pytest.fixture()
def serve_apps(port, apps):
    server = pn.serve(apps, port=port, threaded=True, show=False)
    time.sleep(1)  # Wait for server to start
    yield server
    try:
        server.stop()
    except AssertionError:
        pass  # tests may already close this


@pytest.fixture(scope="session")
def install_test_data():
    ecodata.install_test_datasets()

    return ecodata.datasets.dataset_utils._module_path / "test_datasets"


@pytest.fixture
def make_test_frame_dirs(install_test_data):
    data_path = install_test_data
    print(f"fixture_data_path: {data_path!r}")

    # Copy the input test frames to a new directory with a weird filepath
    # For testing weird filepaths are handled correctly
    shutil.copytree(test_frames_dir, test_frames_dir_weird, dirs_exist_ok=True)

    return test_frames_dir_weird



@pytest.fixture
def track_explorer():
    return TracksExplorer()


@pytest.fixture
def gridded_data_explorer():
    return GriddedDataExplorer()


@pytest.fixture
def subsetter():
    return Subsetter()
