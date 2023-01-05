# Welcome to ECODATA-Prepare's documentation!

## Overview

ECODATA-Prepare is a set of Python-based apps to access and process remote sensing and other environmental data products and prepare them for further use along with animal movement data. Output from ECODATA Prepare is designed to be used as input of environmental data layers for ECODATA-Animate, and can also be used as input to resource, habitat and step selection models.

Within ECODATA-Prepare,

- The **Tracks Explorer** app visualizes movement track location points and point densities with several static maps, allows selecting a spatial frame around the track points that is relevant for analysis, and prepares a .geojson file that can be used in the NASA EARTHDATA AppEEARS interface to extract many different remote sensing data products within this research area. This app also provides the longitude-latitude coordinates of the frame around the research area, which can be used to request data from ECMWF or many other environmental data sources, which can be converted to NETCDF format if needed for further steps described below.

- The **Gridded Data Explorer** app allows you to interpolate and subset the temporal and spatial resolutions of environmental data in the form of a temporal stack (or single static map) NetCDF file. It can also read in several other data formats (which could be resaved as NetCDF). You can also read GIS polygons as shapefiles and mask the environmental data outside or inside the polygons. It subsequently calculates data summaries by period and polygon.

- The **Subsetter** app allows you to clip relevant features out of large GIS files.

- The **Movie Maker** app produces an animation file out of a stack of static maps which were produced by the ECODATA-Animate program.

# Contents

```{toctree}
---
maxdepth: 2
---
installation
environment_files
user_guide/index
support
```

# Indices and tables

* {ref}`genindex`
* {ref}`modindex`
* {ref}`search`
