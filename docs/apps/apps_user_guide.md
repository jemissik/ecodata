# Tracks explorer

## App features

- Visualize Movebank tracks data using either an aggregated point density map (good for visualizing patterns) or a
standard plot of all points. Can select from a variety of map tiles to use as a background.
- Automatically generate a spatial frame around the track points and prepare a .geojson file that can be used in the
NASA AppEEARS interface to extract remote sensing products for the research area.

## Using the app
1. Under "Select File", paste the full filepath to a .csv file of Movebank track data. In the current version, this .csv
file needs to use Movebank's format.
2. Click "Load data". An interactive map will appear of your dataset, where you can pan/zoom to explore the dataset.
3. You can change the background map tile using the drop-down menu in the sidebar.
4. You can turn on/off the point density aggregation feature by toggling the "Datashade tracks" checkbox in the sidebar.
Note that for very large datasets, plotting will be slow if the "Datashade tracks" option is turned off.
5. A boundary for the tracks dataset is shown on the map in a red outline. The default option uses a rectangular
boundary with a buffer size of 0.1 (relative scale to the tracks extent). You can adjust the buffer size or change the
boundary shape to a convex hull using the widgets in the sidebar.
6. To save the boundary to a .geojson file, simply click the "Save extent" button. If you want, you can edit the
location/file name before you save the file.


## Requesting environmental data from NASA AppEEARS

Once you have a .geojson file from the Tracks Explorer app, you can submit a request for NASA data.
- Go to [NASA AppEEARS](https://appeears.earthdatacloud.nasa.gov). You will need to make an account to request data.
- In the top menu bar, click "Extract > Area".
- Click "Start new request"
- In the new request page, you can upload the .geojson file, select a date range, and select the data layers you want.
- In "Output options" at the bottom of the page, select “NetCDF-4” as the file format and “Geographic” as the projection.

# Gridded data explorer

## Using the app
1. Under "Input environmental dataset", paste the full filepath to a netcdf (.nc) file (e.g. from NASA AppEEARS).
2. Click "Load data". The app will auto-detect the variables for time, latitude, and longitude in the dataset. If your
dataset has variable names the auto-detection doesn't recognize, you can specify them manually.
3. In the "Variable of interest" dropdown, select the variable you would like to explore.
4. Click "Update variable names". Plots of the data will appear.
5. At the top of the area with the plots, there are two tabs ("Charts" and "Data"). If you click the "Data" tab, you can
see more information about the .nc file (e.g. all of the variables in the file, dimensions of the dataset,
variable metadata including long names and units)
6. Polygon files: Optionally, you can input a polygon file if you want to select/mask data inside polygon boundaries.
7. Time filtering: There are time filtering options in the sidebar if you want to filter the data for a specific time range or time
conditions (e.g. daytime only, specific seasons). Select your options and then click "Update filters". If you want to
remove the filters you have applied, click "Revert filters".
8. Time resampling: Under "Processing options" in the sidebar, select the desired time frequency and click "Resample
time".
9. Spatial coarsening: Under "Processing options" in the sidebar, select a window size for coarsening and click "Coarsen
dataset".
10. You can save your filtered/processed dataset in the "Output file" pane.
11. Summary statistics: First select grouping options in the "Calculate statistics" pane, then click "Calculate
statistics". A table of the summary statistics will appear, and you have the option to save this table to a .csv file.


# Subsetter

## Using the app

1. Paste the full filepath to a GIS dataset (e.g., a shapefile or filegeodatabase).
2. There are three options for subsetting: specifying bounding box coordinates, providing a movebank track dataset (in
which case the app will automatically draw a boundary around the track data), or providing a polygon to use as a
boundary. Select the option you want and the settings specific to that option.
3. By default, the app will plot the subsetted dataset once it is created, but if you don't want to plot the dataset,
uncheck the box for "Show plot of subset".
3. Change the name of the output file if you wish, then click "Create subset". A file will be saved for the subsetted
dataset, and a plot will appear unless you turned the plotting option off.

# Movie maker

The movie maker app is a simple utility to make a video from a stack of frames.

## Using the app
1. Paste the full path to the folder of the frames.
2. Select the frame rate for the video (frames per second).
3. Change the output filename if you want.
4. Click "Make movie"