"""
Datasets and a few corresponding utilities for the pymovebank package. A few small test datasets are included with the
package in the ``small_datasets`` directory. For convenience, other datasets can be installed by the user to the
``user_datasets`` directory, which is ignored by git.
"""

from pymovebank.datasets.dataset_utils import (available, get_path, install_dataset,
                                               install_test_datasets, install_roads_dataset)
