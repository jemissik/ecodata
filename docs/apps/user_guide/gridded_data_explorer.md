# Gridded data explorer

![gridded_data_explorer](../images/gridded_data_explorer.png)

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
