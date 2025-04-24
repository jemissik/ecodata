# Welcome to ECODATA-Prepare!

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

The functions underlying the ECODATA-Prepare apps can also be used directly as a python package (i.e. without the GUI inferface). The package documentation is here

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
