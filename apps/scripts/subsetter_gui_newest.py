# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.14.0
#   kernelspec:
#     display_name: Python 3 (ipykernel)
#     language: python
#     name: python3
# ---

# %% pycharm={"name": "#%%\n"}
import geopandas as gpd
import pymovebank as pmv
import xarray as xr
import hvplot.xarray  # noqa
import hvplot.pandas  # noqa
import spatialpandas as spd
import numpy as np
import panel as pn
import panel.widgets as pnw
import holoviews as hv
import time
import param

import param
import cartopy.crs as ccrs
from panel.io.loading import start_loading_spinner, stop_loading_spinner
from pymovebank.panel_utils import param_widget


# from holoviews.operation.datashader import datashade, shade, dynspread, spread


pn.extension(template='fast-list', loading_spinner='dots', loading_color='#00aa41', sizing_mode="stretch_width")


# %% pycharm={"name": "#%%\n"}
class Subsetter(param.Parameterized):

    # Input GIS file
    input_file = param_widget(pn.widgets.TextInput(placeholder='Select a file...', name='GIS file'))

    # Widgets common to all selection options
    buffer = param_widget(pn.widgets.EditableFloatSlider(name='Buffer size',
                                                                start=0, end=1, step=0.01, value=0,
                                                                sizing_mode='fixed'))
    clip = param_widget(pn.widgets.Checkbox(name='Clip features', value=True, align='end'))
    output_file = param_widget(pn.widgets.TextInput(placeholder='Choose an output file...',
                                                    value='subset.shp', name='Output file'))

    #Subset type options
    option_picker = param_widget(pn.widgets.RadioButtonGroup(name='Subsetting options',
                                                            options={"Bounding Box": "bbox",
                                                                    "Track Points": "track_points",
                                                                    "Bounding Geometry": "bounding_geom"}))

    # bbox options
    bbox_latmin = param_widget(pn.widgets.FloatInput(name='Lat min',
                                                     value=0, step=1e-2, start=-90, end=90, sizing_mode='fixed'))
    bbox_latmax = param_widget(pn.widgets.FloatInput(name='Lat max',
                                                     value=0, step=1e-2, start=-90, end=90, sizing_mode='fixed'))
    bbox_lonmin = param_widget(pn.widgets.FloatInput(name='Lon min',
                                                     value=0, step=1e-2, start=-180, end=180, sizing_mode='fixed'))
    bbox_lonmax = param_widget(pn.widgets.FloatInput(name='Lon max',
                                                     value=0, step=1e-2, start=-180, end=180, sizing_mode='fixed'))

    # Track file options
    tracks_file = param_widget(pn.widgets.TextInput(placeholder='Select a file...', name='Track points file'))
    boundary_type_tracks = param_widget(pn.widgets.RadioBoxGroup(name='Boundary type', options={'Rectangular': 'rectangular',
                                                                                                'Convex hull': 'convex_hull'}))

    # Bounding geom options
    bounding_geom_file = param_widget(pn.widgets.TextInput(placeholder='Select a file...', name='Bounding geometry file'))
    boundary_type_geom = param_widget(pn.widgets.RadioBoxGroup(name='Boundary type', options={'Rectangular': 'rectangular',
                                                                                                'Convex hull': 'convex_hull',
                                                                                                'Exact':'mask'}))
    show_plot = param_widget(pn.widgets.Checkbox(name='Show plot', value=True, align='end'))

    # Go button
    create_subset_button = param_widget(pn.widgets.Button(name='Create subset', button_type='primary', sizing_mode='fixed'))

    # Status
    status_text = param.String('Ready...')

    def __init__(self, **params):
        super().__init__(**params)

        # Reset names
        self.input_file.name = 'GIS file'
        self.buffer.name = 'Buffer size'
        self.clip.name = "Clip features at boundary edge"
        self.output_file.name = "Output file"
        self.bbox_latmin.name = "Lat min"
        self.bbox_latmax.name = "Lat max"
        self.bbox_lonmin.name = 'Lon min'
        self.bbox_lonmax.name = "Lon max"
        self.tracks_file.name = 'Track points file'
        self.boundary_type_tracks.name = 'Boundary type'
        self.bounding_geom_file.name = 'Bounding geometry file'
        self.boundary_type_geom.name = 'Boundary type'
        self.show_plot.name = 'Show plot of subset'
        self.create_subset_button.name = 'Create subset'

        # Widget groups
        self.bbox_widgets = pn.Column(pn.Row(self.bbox_latmin, self.bbox_latmax),
                                      pn.Row(self.bbox_lonmin, self.bbox_lonmax))

        self.track_points_widgets = pn.Column(self.tracks_file, self.boundary_type_tracks, self.buffer)

        self.bounding_geom_widgets = pn.Column(self.bounding_geom_file, self.boundary_type_geom, self.buffer)

        self.shared_widgets = pn.Column(self.clip, self.output_file, self.show_plot, self.create_subset_button)


        self.option_picker_mapper = {"bbox": self.bbox_widgets,
                            "track_points": self.track_points_widgets,
                            "bounding_geom": self.bounding_geom_widgets}

        self.status = pn.pane.Alert(self.status_text)
        # View
        self.view_objects = {'plot':0,
                             'input_file':1,
                             'option_picker':2,
                             'option_widgets':3,
                             'shared_widgets':4,
                             'status':5}

        self.view = pn.Column(
            pn.pane.Markdown("## Create a subset!"),
            self.input_file,
            self.option_picker,
            self.bbox_widgets,
            self.shared_widgets,
            self.status
        )

    @param.depends("status_text", watch=True)
    def update_status_text(self):
        self.view[self.view_objects['status']] = pn.pane.Alert(self.status_text)
        # self.view[-1] = pn.pane.Alert(self.status_text)

    @param.depends("option_picker.value", watch=True)
    def _update_widgets(self):
        self.status_text = 'updated widgets'
        option = self.option_picker.value
        widgets = self.option_picker_mapper[option]
        self.view[self.view_objects['option_widgets']] = widgets

    def get_args_from_widgets(self):
        args = dict(filename=self.input_file.value,
            clip=self.clip.value,
            outfile=self.output_file.value)

        if self.option_picker.value=='bbox':
            args['bbox'] = (self.bbox_lonmin.value, self.bbox_latmin.value,
                            self.bbox_lonmax.value, self.bbox_latmax.value)
        elif self.option_picker.value=='track_points':
            args['track_points'] = self.tracks_file.value
            args['boundary_type'] = self.boundary_type_tracks.value
            args['buffer'] = self.buffer.value,

        elif self.option_picker.value=='bounding_geom':
            args['bounding_geom'] = self.bounding_geom_file.value
            args['boundary_type'] = self.boundary_type_geom.value
            args['buffer'] = self.buffer.value,

        return args

    @param.depends('create_subset_button.value', watch=True)
    def create_subset(self):

        self.status_text = 'Creating subset...'
        start_loading_spinner(self.view)
        try:
            args = self.get_args_from_widgets()
            subset = pmv.subset_data(**args)
            if len(subset['subset']) == 0:
                self.status_text = "No features in subset"
            else:
                self.status_text = 'Subset created!'
                if self.show_plot.value:
                    plot = pmv.plot_subset(**subset)
                    self.view[self.view_objects['plot']] = pn.pane.Matplotlib(plot)
                else:
                    self.view[self.view_objects['plot']] = pn.pane.Markdown(' ## Subset saved to output directory')
        except:
            self.view[self.view_objects['plot']] = pn.pane.Markdown("## Create a subset!")
            self.status_text = 'Error creating subset. Make sure all necessary inputs are provided.'
        finally:
            stop_loading_spinner(self.view)

if __name__ == "__main__":
    subsetter = Subsetter()
    pn.Row(subsetter.view).servable()
