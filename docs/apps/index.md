# Welcome to ECODATA-Prepare!

This anonymized version of the repository is submitted as part of a double-blind peer review. Note that identifying
information, including links to online documentation, respsitories, and datasets, has been removed from the code and
documentation.

## Overview

ECODATA-Prepare is a set of Python-based apps to access and process remote sensing and other environmental data products and prepare them for further use along with animal movement data. ECODATA-Prepare is designed to be used along with the ECODATA-Animate tool to create customized movies of animal movement data, and can also be used to prepare input to resource, habitat and step selection models. Development is supported by MathWorks® and the NASA Earth Science Division, Ecological Forecasting Program, as part of XXXXX project.

Within ECODATA-Prepare,

- The [**Tracks Explorer App**](user_guide/tracks_explorer) visualizes movement track location points and point densities with several static maps, allows selecting a spatial frame around the track points that is relevant for analysis, and prepares a .geojson file that can be used in the NASA EARTHDATA AppEEARS interface to extract many different remote sensing data products within this research area. This app also provides the longitude-latitude coordinates of the frame around the research area, which can be used to request data from ECMWF or many other environmental data sources, which can be converted to NetCDF format if needed for further steps described below.

- The [**Gridded Data Explorer App**](user_guide/gridded_data_explorer) allows you to interpolate and subset the temporal and spatial resolutions of environmental data in the form of a temporal stack (or single static map) NetCDF file. It can also read in several other data formats (which could be resaved as NetCDF). You can also read GIS polygons as shapefiles and mask the environmental data outside or inside the polygons. It subsequently calculates data summaries by period and polygon.

- The [**Subsetter App**](user_guide/subsetter) allows you to clip relevant features out of large GIS files.

- The [**Movie Maker App**](user_guide/movie_maker) produces an animation file out of a stack of static maps which were produced by the ECODATA-Animate program.

XXXXX of how to use Movebank, MoveApps, ECODATA-Animate and ECODATA-Prepare to discover and process tracking data, remote sensing data, shapefiles and other layers to create custom visualizations and input for ecological analysis. To try the software before working with your own data, see our example based on publicly-available data sources.

For help, submit a GitHub issue or contact XXXXX.

![ecodata_workflow](https://www.movebank.org/cms/serve/images/ecodata_workflow.png)

## Python package

The functions underlying the ECODATA-Prepare apps can also be used directly as a python package (i.e. without the GUI inferface). The package documentation is XXXXX

## Reviewers: How to Install and Run the Anonymous Version of the Code

You will need to have conda installed on your computer. If you are on Windows, sometimes the Anaconda terminal needs to be run as an administrator when installing packages, depending on how your system is set up.

With conda installed, from the root of the repository, run the following command to install all of the dependencies:

```bash
conda env create --file ecodata-env.yml
````

Then activate the environment:

```bash
conda activate eco
```

To install the test data bundle, run the following command:

```bash
python -c "import ecodata as eco; eco.install_test_datasets()"
```

Which will install the test data bundle under {repo dir}/ecodata/datasets/test_datasets

To run the app, run the following command:

```bash
python -m ecodata.app
```

This should automatically launch the app in your default web browser. If it doesn't, you can manually navigate to `http://localhost:5006` in your web browser. 

From here, you can choose which app you want to run. The test data bundle includes example data for each of the apps, which you can use to test the functionality of the app. Once you click on an app, it should load the app up, and clicking Home in the top left corner will take you back to the app selection page.

For the Gridded Data Explorer App, you can also use {repo dir}/ecodata/datasets/test_datasets/NASA_public_caribou.nc for the dataset. Load the dataset, and select a variable of interest, then create the plot. Then use {repo dir}/ecodata/datasets/test_datasets/public_caribou_lakes/public_caribou_lakes.shp for the polygon file, which with the filters on the left allow masking of the first dataset.

For the Tracks Explorer App, you can use {repo dir}/ecodata/datasets/test_datasets/public_caribou_tracks.csv

For the Subsetter App, you can use {repo dir}/ecodata/datasets/test_datasets/public_caribou_lakes/public_caribou_lakes.shp for the initial dataset, and then create a bounding box, bounding geometry, or if you want to use track points, you can use {repo dir}/ecodata/datasets/test_datasets/public_caribou_tracks.csv

For the Movie Maker App, you can use {repo dir}/ecodata/datasets/test_datasets/animation_test_frames for the test animation frames

# Contents

```{toctree}
---
maxdepth: 2
---
installation
user_guide/index
environmental_data
support
developer_guide
```

# Indices and tables

* {ref}`genindex`
* {ref}`modindex`
* {ref}`search`
