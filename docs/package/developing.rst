Developer guide
===============

Installation options
--------------------
* To install ecodata in editable mode::

        conda env create -n eco-dev --file ecodata-env.yml
        conda activate eco-dev
        conda env update -n eco-dev -f ecodata-dev-env.yml --prune
        python -m pip install -e . --no-deps

* To do a full install (not editable):

  * Create a clean build environment (including only ecodata's requirements, not ecodata)::

        conda env create --file ecodata-clean-build-env.yml

  * If you want to keep a copy of the clean environment, make a new copy where ecodata will be installed::

        conda create --clone eco-build-clean --name eco-build
        conda activate eco-build

  * build and install ecodata (must be in the repository's root directory)::

        python -m build
        pip install dist/ecodata-0.0.0.tar.gz


Using pyinstaller
-----------------

* ecodata must be actually installed (can't use editable mode). Follow the instructions for the full install above.
* run pyinstaller for the subsetting script::

        pyinstaller subset_script.spec --clean