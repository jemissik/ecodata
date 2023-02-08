import pytest
import panel as pn
import time
import shutil
import pymovebank
from pymovebank.app.apps import applications


PORT = [6000]

test_data_dir = pymovebank.datasets.dataset_utils._module_path / "test_datasets"
test_frames_dir = test_data_dir / "animation_test_frames"
test_frames_dir_weird = test_data_dir / "animation & test & frames"

test_output_dir = pymovebank.datasets.dataset_utils._module_path / "test_datasets" / "output"
test_output_dir_weird = pymovebank.datasets.dataset_utils._module_path / "test_datasets" / "output & weird"

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
    time.sleep(1) # Wait for server to start
    yield server
    try:
        server.stop()
    except AssertionError:
        pass  # tests may already close this


@pytest.fixture(scope="session")
def install_test_data():
    pymovebank.install_test_datasets()

    return pymovebank.datasets.dataset_utils._module_path / "test_datasets"


@pytest.fixture
def make_test_frame_dirs(install_test_data):
    data_path = install_test_data
    print(f"fixture_data_path: {data_path}")

    # Copy the input test frames to a new directory with a weird filepath
    # For testing weird filepaths are handled correctly
    shutil.copytree(test_frames_dir, test_frames_dir_weird, dirs_exist_ok=True)

    return test_frames_dir_weird
