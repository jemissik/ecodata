# Installation

## Install Python

To use `ecodata`, you must first have Python and the conda package manager
installed. There are two options for this:
- **Install Anaconda**: This is the recommended option for those who are new to
Python. Anaconda comes with the Spyder IDE, which provides an interface similar to
RStudio and MATLAB, and Jupyter Notebook, which can be used to run interactive Python
notebooks such as those in ecodata's examples. It also includes a graphical interface (Anaconda Navigator) for launching applications and managing conda environments without using the command line. To install Anaconda, see [directions for installing Anaconda](https://docs.anaconda.com/anaconda/install/index.html).
- **Install Miniconda**: If you want a more minimal installation without any extra
packages, would prefer to handle installing a Python IDE yourself, and would prefer
to work from the command line instead of using the graphical interface provided
by Anaconda Navigator, you might prefer to install Miniconda rather than Anaconda. Miniconda is a minimal version of Anaconda that includes just conda, its dependencies,
and Python. To install Miniconda, see [directions for installing Miniconda](https://docs.conda.io/en/latest/miniconda.html).

## Install ecodata

After Anaconda/Miniconda is installed, you can install ecodata in a new conda
environment.

### Download ecodata

Clone the ecodata repository from [ecodata's GitHub page](https://github.com/jemissik/ecodata/).

### Install ecodata and its dependencies

These instructions will build and install ecodata in
editable mode in a new conda environment, which will let you access the package
using `import ecodata` as you would with any other Python package, without
needing to worry about where the source code directory is located.

- **Note**: Since ecodata is in the early stages of development, you will need to install the package from the command line. Once the project is a bit further along,
it will be published on conda-forge and you will also have the option to install it
using Anaconda Navigator's graphical interface.


**To install using the command line**:
1. Use `cd` to navigate into the ecodata directory that you cloned from GitHub.
2. Create ecodata's conda environment:

    ```
    conda env create --file ecodata-env.yml
    ```
    This will build and install ecodata in editable mode in a new conda environment.
3. To activate the conda environment, run:
    ```
    conda activate ecodata-env
    ```
    See this [cheat sheet for working with conda](https://docs.conda.io/projects/conda/en/latest/_downloads/843d9e0198f2a193a3484886fa28163c/conda-cheatsheet.pdf) for
    a helpful list of conda commands.

- **Note**: If you are already using `pip` to manage your Python packages, note
that it is recommended to use `conda` instead of `pip` to install the required
packages for ecodata. If you use `pip` the dependencies might not be installed
correctly. See [here](https://geopandas.org/en/stable/getting_started/install.html#installing-with-pip) for more information.


## Install the test data bundle

To install the test data bundle:

```python
import ecodata as eco
eco.install_test_datasets()
```

The test data bundle can also be downloaded directly [here](https://drive.google.com/drive/folders/1eAqSKblWpM5kqqEByf6YaiRWywZFMKvJ?usp=sharing)
