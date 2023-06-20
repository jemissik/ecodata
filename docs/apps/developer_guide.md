# Developer guide

## Contributing
Check out this [simple guide for using git](https://rogerdudler.github.io/git-guide/).

1. Create a new branch for your contributions.
2. Commit your changes in this branch.
3. [Open a pull request](https://github.com/jemissik/ecodata/pulls) to merge changes from your branch into the
repository's ``develop`` branch.


## Documentation

Documentation for this project is created using Sphinx and is hosted at Read the Docs (https://ecodata-apps.readthedocs.io/). The source files
for these pages are located in the [docs/apps folder](https://github.com/jemissik/ecodata/tree/develop/docs/apps) of the repository. To edit the documentation, edit the markdown files in this folder (or sub-folders). Note that the ``docs/index.md`` file specifies the contents for the docs site. If a sub-folder has a ``index.md`` file, that file specifies the contents for that section of the docs site (e.g. ``docs/user_guide/index.md``). If files are added or removed, the corresponsing index files will also need to be updated.

### Building the docs
After editing the pages, you can look at a build of the pages to see how things will actually look in the docs website. There are two options for this:
- Option 1: [Open a pull request](https://github.com/jemissik/ecodata/pulls), and Read the Docs will build a preview of the docs pages. A link to the build can be found near the bottom of the page of the PR, in the merge checks section (once the build is finished, click on "Details" for the docs/readthedocs.org:ecodata-animate item.
You may have to click "Show details" next to where it says "All checks have passed"). You can push additional commits to the open PR if you want to change anything after seeing the preview build.
- Option 2: Build the docs locally. You will need to have python and the docs requirements installed.

    - To install the doc requirements: [Developing Installation instructions](#developer-installation)
    - Then Run ``PROJECT=apps sphinx-build -b html docs docs/_build/apps`` from the repo's root directory
    - To view the build, open the ``index.html`` in the docs/_build/apps directory that was created.

### Versions of the docs
- Read the Docs builds multiple versions of the documentation (for different branches of the repository). In the bottom corner of the docs pages, there is a box indicating which version you are viewing. You can click on that box to pick a different version.


### Developer installation

1. Clone the repository, and navigate to the root directory of the cloned repo
2. Create the conda environment for the package:

    ```
    conda env create --file ecodata-env.yml --name eco-dev
    ```
3. Activate the `eco-dev` environment (this will install the package in editable mode):

    ```
    conda activate eco-dev
    ```
4. With the `eco-dev` environment activated, install the additional dev requirements (needed for docs, testing, code style, etc):

    ```
    conda env update --name eco-dev --file ecodata-dev-env.yml --prune
    python -m pip -e . --no-deps
    ```