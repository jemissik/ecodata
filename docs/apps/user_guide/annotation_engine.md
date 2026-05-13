# Annotation Engine

## App features

With the Annotation Engine App, you can
- Annotate movement data with environmental variables from gridded environmental datasets.
- Load movement data and environmental data from supported formats such as NetCDF and GeoTIFF.
- Select environmental variables for annotation.
- Match movement records with environmental values by location and time.
- Use different annotation approaches for continuous variables and categorical or quality-control variables.
- Apply spatial and temporal matching or interpolation methods where supported.
- Optionally apply scale factor and offset corrections to continuous variables.
- Export annotated movement data for further analysis or visualization.

## Using the app

1. If you haven't already, prepare a local movement data file and the environmental datasets you want to use for annotation.
2. Launch the Annotation Engine App.
3. Select the movement data file. The file should contain location and time information compatible with the ECODATA movement data format.
4. Load the environmental dataset or datasets. Depending on the workflow, these may be NetCDF or GeoTIFF files.
5. Select the environmental variables that should be added to the movement records.
6. Specify whether selected variables should be treated as continuous variables or categorical / quality-control variables.
7. Select the annotation method and, if available, the spatial or temporal interpolation options.
8. If using continuous variables with scale factor or offset values, set these options before running the annotation.
9. Run the annotation process.
10. Review the status messages and save the annotated movement data file.