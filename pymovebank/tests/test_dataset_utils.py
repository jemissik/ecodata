import pymovebank
import pytest

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
    with pytest.raises(ValueError):
        pymovebank.get_path(invalid_dataset)
