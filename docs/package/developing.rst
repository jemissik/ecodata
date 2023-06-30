Developer guide
===============

Installation options
--------------------
* To install ecodata in editable mode::

        conda env create -n eco-dev --file ecodata-env.yml
        conda activate eco-dev
        conda env update -n eco-dev -f ecodata-dev-env.yml

* To do a full install (not editable):

  * Create a clean build environment (including only ecodata's requirements, not ecodata)::

        conda env create --file ecodata-clean-build-env.yml

  * If you want to keep a copy of the clean environment, make a new copy where ecodata will be installed::

        conda create --clone eco-build-clean --name eco-build
        conda activate eco-build

  * build and install ecodata (must be in the repository's root directory)::

        python -m build
        pip install dist/ecodata-0.0.0.tar.gz


Package Building
--------------------

To release a new version of ecodata, either release from main (full release) or develop (pre-releases like release candidates). Tag a new release or use github's release feature and add release notes.

********************
Releasing to Conda
********************

The following steps currently only work with full releases

Once the release has been made, you will need the release's sha. You can get this from the github release with this command from a linux or mac (replace {tag of release} with the actual tag::

    curl -sL https://github.com/jemissik/ecodata/archive/refs/tags/{tag of release}.tar.gz | openssl sha256

Follow `Conda | example workflow for updating a package`_ for the `ecodata feedstock`_.

You will often only need to update the version number and sha. But if any requirements were updated (added, removed, or version pin changes) those will need to be updated as well.

Once you have released to Conda (and the version is released to conda-forge, not just cf-staging) and it has been there for maybe 10-15 minutes so you don't run into a cache problem, then you can move to the next step.

*********************************
Build Conda Constructor Assets
*********************************

In the ecodata repo actions tab, click on the conda_constructor CI tab on the left, and you will see a "Run workflow" button appear on the top right. Click on that button, select the branch you are running your release is from (main for full releases), and type in the release tag (ex. v0.42.0). Once the workflow as finished running, It should upload the conda constructor assets to that release.


.. _`Conda | example workflow for updating a package`: https://conda-forge.org/docs/maintainer/updating_pkgs.html#example-workflow-for-updating-a-package
.. _`ecodata feedstock`: https://github.com/conda-forge/ecodata-feedstock