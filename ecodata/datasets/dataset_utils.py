"""
Datasets for ecodata
"""

import os
import shutil
from pathlib import Path

import requests
import wget
import gdown

__all__ = ["available", "get_path", "install_roads_dataset"]

_module_path = Path(__file__).parent

_dict_available = {}

def _update_dict_available():

    if (_module_path / "user_datasets").exists():
        _user_datasets_paths = [
            f
            for f in (_module_path / "user_datasets").iterdir()
            if not (
                str(f.name).startswith(".")
                or str(f.name).startswith("__")
                or str(f.name) == "temp_downloads"
            )
        ]
        _user_datasets_names = [f.name for f in _user_datasets_paths]

    else:
        _user_datasets_paths = [None]
        _user_datasets_names = [None]


    if (_module_path / "test_datasets").exists():
        _test_datasets_paths = [
            f
            for f in (_module_path / "test_datasets").iterdir()
            if not (str(f.name).startswith(".") or str(f.name).startswith("__"))
        ]
        _test_datasets_names = [f.name for f in _test_datasets_paths]

    else:
        _test_datasets_paths = [None]
        _test_datasets_names = [None]

    global _dict_available
    _dict_available = dict(zip(_user_datasets_names, _user_datasets_paths)) | dict(
        zip(_test_datasets_names, _test_datasets_paths)
    )

def available():
    """
    Get the list of available datasets installed in ecodata.datasets
    """
    _update_dict_available()
    return list(_dict_available.keys())


def get_path(dataset):
    """
    Get the path to the datasets installed in ecodata.datasets.

    Parameters
    ----------
    dataset : str
        The name of the dataset. See ``ecodata.data.available`` for
        all options.
    """
    if dataset in available():
        path = _dict_available[dataset]
        if path.suffix == ".zip":
            path = "zip://" + str(path)
        else:
            path = str(path)
        return path
    else:
        msg = "The dataset '{data}' is not available. ".format(data=dataset)
        msg += "Available datasets are {}".format(", ".join(available()))
        raise ValueError(msg)

def install_dataset(data_path):
    """
    Install a dataset in the ecodata. This function copies the dataset to the data directory of the installed
    ecodata module, so that it will be accessible as a dataset in ``ecodata.datasets.available``

    Parameters
    ----------
    data_path : str
        Path to the file or directory of the dataset to be installed.
    """

    user_data_path = Path(data_path)
    installed_filepath = (_module_path / "test_datasets" / user_data_path.name)

    if user_data_path.is_dir():
        shutil.copytree(user_data_path, installed_filepath)
    elif user_data_path.is_file():
        shutil.copy(user_data_path, installed_filepath)


def install_test_datasets():
    """
    Install the bundle of test datasets
    """

    url = 'https://drive.google.com/drive/folders/1eAqSKblWpM5kqqEByf6YaiRWywZFMKvJ?usp=sharing'
    install_path = Path(_module_path) / "test_datasets"

    # Delete the test data bundle if it exists
    shutil.rmtree(install_path, ignore_errors=True)

    try:
        install_path.mkdir(exist_ok=True)
        downloaded_files = gdown.download_folder(url, output=str(install_path))
        print("Installed test datasets.")
    except BaseException as e:
        print(f"\nFailed to install datasets because of {e!r}")


def install_roads_dataset():
    """
    Install the Region 1 (North America) GRIP roads dataset in shapefile format.

    References
    ----------
    Meijer, J.R., Huijbegts, M.A.J., Schotten, C.G.J. and Schipper, A.M. (2018):
    Global patterns of current and future road infrastructure. Environmental Research
    Letters, 13-064006. Data is available at www.globio.info

    .. todo::
        - Add option to download the global dataset in gdb format instead
    """

    # Remove any partially downloaded datasets
    _remove_temp_downloads()

    roads_url = (
        "https://dataportaal.pbl.nl/downloads/GRIP4/GRIP4_Region1_vector_shp.zip"
    )

    # Confirm user wants to proceed with download
    while True:
        # Get size of requested file
        filesize = requests.head(roads_url).headers["Content-Length"]
        print()
        in_GB = int(filesize) / (1000**3)
        response = input(
            "The download is {:.2f}. Do you want to proceed? [y/n]".format(in_GB)
        )
        if response.lower() == "y":
            print(
                "Installing North America roads dataset. It's a large download and will take a few mintues..."
            )
            install_path = Path(_module_path) / "user_datasets"
            install_path.mkdir(exist_ok=True)

            # Run wget from temp downloads directory
            download_path = install_path / "temp_downloads"
            download_path.mkdir(exist_ok=True)
            os.chdir(download_path)
            try:
                filename = wget.download(roads_url, out=str(install_path))
                filepath = install_path / Path(filename)
                print("Installed dataset at: " + str(filepath))
            except BaseException as e:
                download_path = Path(_module_path) / "user_datasets/temp_downloads"
                shutil.rmtree(download_path)
                print(f"\nFailed to download dataset because of {e!r}")
            break
        elif response.lower() == "n":
            print("Download cancelled.")
            break
        else:
            print("Invalid answer. Please answer [y/n]")


def _remove_temp_downloads():
    """
    Delete any partially downloaded files from failed attempts to install datasets.
    """
    download_path = Path(_module_path) / "user_datasets/temp_downloads"
    if os.path.exists(download_path) and os.listdir(download_path):
        print("Found partially downloaded files in ecodata.datasets.")
        while True:
            response = input(
                "Do you want to delete these files before you download a new dataset? [y/n]"
            )
            if response.lower() == "y":
                shutil.rmtree(download_path)
                print("Removed files.")
                break
            elif response.lower() == "n":
                break
            else:
                print("Invalid answer. Please answer [y/n]")
