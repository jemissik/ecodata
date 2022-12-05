Features
========

Data aggregation tool
---------------------

Data selection, resolution transformations, and aggregation functions for gridded datasets.

Workflow:

1. Data selection: Select data that will be included in the analysis (times of interest and spatial areas of interest)

  * Time selection: Time ranges or conditional time selection (e.g. winter only, certain years only)
  * Spatial selection: Select data within polygon boundaries, or mask data outside polygon boundaries

2. Resolution and re-gridding

  * Thin
  * Coarsen
  * Interpolate
  * Re-grid to align with another dataset

3. Group selection

  * Time selection
  * Zonal statistics

4. Aggregation

  * Mean, standard deviation, counts
  * Counts exceeding threshold
  * First day threshold exceeded

Utilities
---------

* Subsetting tool: Efficiently create subsets from very large geographic datasets, and visualize the results

* Convenience functions for getting information (e.g. metadata, number of records, coordinate system, spatial extent)
  from a large dataset without reading the dataset into memory

* File conversions

    * convert .grib files to netcdf files
    * convert a stack of geotiff files to a single netcdf