# NC Builder

## App features

With the NC Builder App, you can
- Prepare and standardize NetCDF files for use in ECODATA annotation workflows.
- Load one or more NetCDF files from local folders.
- Inspect available variables, coordinates, dimensions, and time information.
- Select target variables and assign standard coordinate roles such as time, latitude, longitude, and vertical level.
- Combine files by time, by level, or by both time and level, depending on the structure of the source data.
- Optionally apply spatial and temporal subsetting.
- Export a standardized NetCDF file that can be used by ECODATA annotation apps.

## Using the app

1. If you haven't already, prepare the NetCDF files that need to be combined or standardized.
2. Launch the NC Builder App.
3. Select the input folder or input files containing the NetCDF data.
4. Choose the combine mode, such as combining by time, by vertical level, or by both time and level.
5. Inspect the detected variables and coordinates.
6. Select the target variable and assign the correct coordinate fields for time, latitude, longitude, and, if needed, vertical level.
7. If needed, set spatial or temporal subset options.
8. Specify the output file name and location.
9. Click the build button to create the standardized NetCDF file.
10. Review the status messages and check the output file before using it in annotation workflows.