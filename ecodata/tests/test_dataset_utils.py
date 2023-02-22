import pytest

import ecodata


def test_that_test_data_bundle_installs(install_test_data):

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

    assert data_files.issubset(ecodata.available())

def test_that_installing_test_bundle_works_if_already_installed(install_test_data):

    # Run the install method again
    ecodata.install_test_datasets()

@pytest.mark.skip(reason="Needs updating for new test datasets")
def test_that_valid_dataset_returns_correct_path():
    # Given a valid dataset
    dataset = "Y2Y_Region_Boundary"

    # When get_path is called
    path = ecodata.get_path(dataset)

    # path is correct
    correct_path = str(
        ecodata.datasets.dataset_utils._module_path / "small_datasets" / dataset
    )
    assert path == correct_path


def test_that_invalid_dataset_raises_error():
    # Given an invalid dataset
    invalid_dataset = "Fake_Dataset"

    # When get_path is called
    # ValueError is raised
    with pytest.raises((ValueError, TypeError)):
        ecodata.get_path(invalid_dataset)
