Developing
==========

Installation options
--------------------
* To install pymovebank in editable mode::

        conda env create --file pymovebank-dev-env.yml

* To do a full install (not editable):
  * Create a clean build environment (including only pymovebank's requirements, not pymovebank)::

        conda env create --file pymovebank-clean-build-env.yml

  * If you want to keep a copy of the clean environment, make a new copy where pymovebank will be installed::

        conda create --clone pmv-build-clean --name pmv-build
        conda activate pmv-build

  * build and install pymovebank (must be in the repository's root directory)::

        python -m build
        pip install dist/pymovebank-0.0.0.tar.gz


Using pyinstaller
-----------------

* pymovebank must be actually installed (can't use editable mode). Follow the instructions for the full install above.
* run pyinstaller for the subsetting script::

        pyinstaller subset_script.spec --clean