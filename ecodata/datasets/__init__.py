"""
Datasets and a few corresponding utilities for the ecodata package. A few small test datasets are included with the
package in the ``small_datasets`` directory. For convenience, other datasets can be installed by the user to the
``user_datasets`` directory, which is ignored by git.
"""

from ecodata.datasets.dataset_utils import available  # noqa
from ecodata.datasets.dataset_utils import get_path  # noqa
from ecodata.datasets.dataset_utils import install_dataset  # noqa
from ecodata.datasets.dataset_utils import install_roads_dataset  # noqa
from ecodata.datasets.dataset_utils import install_test_datasets  # noqa
