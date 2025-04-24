# Developer guide

## Contributing
Check out this [simple guide for using git](https://rogerdudler.github.io/git-guide/).

1. Create a new branch for your contributions.
2. Commit your changes in this branch.
3. Open a pull request to merge changes from your branch into the
repository's ``develop`` branch.

## Developer installation

1. Install conda. We suggest installing either Miniforge or Miniconda. [See here for installation instructions](https://docs.conda.io/projects/conda/en/stable/).

```{note}
***For Windows***: Use the command prompt that was installed with conda for the next steps. (It will be called "Anaconda Prompt" or "Miniforge Prompt".)
```

```{note}
If you have an existing conda installation, it's strongly recommended to make sure you are using the libmamba solver. [See here for additional information.](https://docs.conda.io/projects/conda/en/stable/release-notes.html#with-this-23-10-0-release-we-are-changing-the-default-solver-of-conda-to-conda-libmamba-solver)
```
2. Clone the repository: XXXXX
3. Navigate to the root directory of the cloned repo.
4. Create the conda environment for the package:

    ```
    conda env create --file ecodata-env.yml --name eco-dev
    ```

5. Install the additional dev requirements (needed for docs, testing, code style, etc), and install the package in editable mode:

    ```
    conda env update --name eco-dev --file ecodata-dev-env.yml
    ```

### Launching the apps

To activate the `eco-dev` environment, run:

```
conda activate eco-dev
```

To launch the apps, run:

```
python -m ecodata.app
```

## Documentation

Documentation for this project is created using Sphinx and is hosted at Read the Docs (XXXXX). The source files
for these pages are located in the docs/apps folder of the repository. To edit the documentation, edit the markdown files in this folder (or sub-folders). Note that the ``docs/index.md`` file specifies the contents for the docs site. If a sub-folder has a ``index.md`` file, that file specifies the contents for that section of the docs site (e.g. ``docs/user_guide/index.md``). If files are added or removed, the corresponsing index files will also need to be updated.

### Building the docs
After editing the pages, you can look at a build of the pages to see how things will actually look in the docs website. There are two options for this:
- Option 1: Open a pull request, and Read the Docs will build a preview of the docs pages. A link to the build can be found near the bottom of the page of the PR, in the merge checks section (once the build is finished, click on "Details" for the XXXXX item.
You may have to click "Show details" next to where it says "All checks have passed"). You can push additional commits to the open PR if you want to change anything after seeing the preview build.
- Option 2: Build the docs locally. You will need to have python and the docs requirements installed.

    - To install the doc requirements: [Developing Installation instructions](#developer-installation)
    - Then Run ``PROJECT=apps sphinx-build -b html docs docs/_build/apps`` from the repo's root directory
    - To view the build, open the ``index.html`` in the docs/_build/apps directory that was created.

### Versions of the docs
- Read the Docs builds multiple versions of the documentation (for different branches of the repository). In the bottom corner of the docs pages, there is a box indicating which version you are viewing. You can click on that box to pick a different version.
