import pymovebank
import pytest

def test_that_test_data_bundle_installs():

    pymovebank.install_test_datasets()

    # Files that should be in the test dataset
    data_files = {
        "ECMWF_public_caribou.nc",
        "public_caribou_tracks.csv",
        "NASA_public_caribou.nc",
        "public_caribou_tracks_clipped.csv",
        "public_caribou_lakes",
        "public_caribou_tracks_extent.geojson",
        "public_caribou_protected_areas",
        "public_caribou_tracks_web.csv",
        "public_caribou_ref.csv",
        "public_caribou_roads"
    }

    assert data_files.issubset(pymovebank.available())

def test_that_installing_test_bundle_works_if_already_installed():

    # Run the install method twice
    pymovebank.install_test_datasets()
    pymovebank.install_test_datasets()

@pytest.mark.skip(reason="Needs updating for new test datasets")
def test_that_valid_dataset_returns_correct_path():
    # Given a valid dataset
    dataset = "Y2Y_Region_Boundary"

    # When get_path is called
    path = pymovebank.get_path(dataset)

    # path is correct
    correct_path = str(
        pymovebank.datasets.dataset_utils._module_path / "small_datasets" / dataset
    )
    assert path == correct_path


def test_that_invalid_dataset_raises_error():
    # Given an invalid dataset
    invalid_dataset = "Fake_Dataset"

    # When get_path is called
    # ValueError is raised
    with pytest.raises((ValueError, TypeError)):
        pymovebank.get_path(invalid_dataset)
