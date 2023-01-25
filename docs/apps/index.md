# Welcome to ECODATA-Prepare!

## Overview

ECODATA-Prepare is a set of Python-based apps to access and process remote sensing and other environmental data products and prepare them for further use along with animal movement data. ECODATA-Prepare is designed to be along with the [ECODATA-Animate](https://ecodata-animate.readthedocs.io/en/latest/) tool to create movies of animal movement data, and can also be used to create input to resource, habitat and step selection models. Development is supported by MathWorks® and the NASA Earth Science Division, Ecological Forecasting Program, as part of [Room to Roam: Y2Y Wildlife Movements](https://ceg.osu.edu/Y2Y_Room2Roam) project.

Within ECODATA-Prepare,

- The [**Tracks Explorer App**](user_guide/tracks_explorer) visualizes movement track location points and point densities with several static maps, allows selecting a spatial frame around the track points that is relevant for analysis, and prepares a .geojson file that can be used in the NASA EARTHDATA AppEEARS interface to extract many different remote sensing data products within this research area. This app also provides the longitude-latitude coordinates of the frame around the research area, which can be used to request data from ECMWF or many other environmental data sources, which can be converted to NETCDF format if needed for further steps described below.

- The [**Gridded Data Explorer App**](user_guide/gridded_data_explorer) allows you to interpolate and subset the temporal and spatial resolutions of environmental data in the form of a temporal stack (or single static map) NetCDF file. It can also read in several other data formats (which could be resaved as NetCDF). You can also read GIS polygons as shapefiles and mask the environmental data outside or inside the polygons. It subsequently calculates data summaries by period and polygon.

- The [**Subsetter App**](user_guide/subsetter) allows you to clip relevant features out of large GIS files.

- The [**Movie Maker App**](user_guide/movie_maker) produces an animation file out of a stack of static maps which were produced by the ECODATA-Animate program.

We recommend the following workflow (you can skip any steps you don't need):
1. Use the [Tracks Explorer App](user_guide/tracks_explorer) to create a .geojson file that defines the area of interest based on your tracking data.
2. Use this file to [request environmental data from NASA AppEEARS](request-nasa-data) to use in subsequent animations or analysis.
3. Use the [Gridded Data Explorer App](user_guide/gridded_data_explorer) to review and further process the environmental data received from NASA AppEEARS.
4. Use the [Subsetter App](user_guide/subsetter) to extract relevant features from very large datasets (for example, global vector GIS data products for features like roads, water bodies or protected areas) to use in subsequent animations or analysis.
5. Use [ECODATA-Animate](https://ecodata-animate.readthedocs.io/en/latest/) to combine your tracking data, environmental data, subsetted vector layers, and other information, configure viewing options, and create a set of images to use as frames for an animation or for further exploration and analysis.
6. Use the [Movie Maker App](https://ecodata-apps.readthedocs.io/en/latest/user_guide/movie_maker.html) to create an animation from the output of ECODATA-Animate.

For help, [submit a GitHub issue](https://ecodata-apps.readthedocs.io/en/latest/support.html) or contact support@movebank.org.

## Python package

The functions underlying the ECODATA-Prepare apps can also be used directly as a python package (i.e. without the GUI inferface). The package documentation is [here](https://pymovebank.readthedocs.io).

# Contents

```{toctree}
---
maxdepth: 2
---
installation
environment_files
user_guide/index
support
developer_guide
```

# Indices and tables

* {ref}`genindex`
* {ref}`modindex`
* {ref}`search`
