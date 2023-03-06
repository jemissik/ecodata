import hvplot.pandas  # noqa
import hvplot.xarray  # noqa
import param
import panel as pn
from ecodata.panel_utils import param_widget

map_tile_options = [
    None,
    "CartoDark",
    "CartoLight",
    "EsriImagery",
    "EsriNatGeo",
    "EsriReference",
    "EsriTerrain",
    "EsriUSATopo",
    "OSM",
    "StamenTerrain",
    "StamenTerrainRetina",
    "StamenToner",
    "StamenTonerBackground",
    "StamenWatercolor",
]


def plot_tracks_with_tiles(
    tracks, tiles="StamenTerrain", datashade=True, cmap="fire", c="r", marker="circle", alpha=0.3
):
    """
    Hvplot map of tracks with background map tiles
    """

    plot = tracks.hvplot.points(
        "location_long",
        "location_lat",
        geo=True,
        tiles=tiles,
        datashade=datashade,
        project=True,
        hover=False,
        cmap=cmap,
        c=c,
        marker=marker,
        alpha=alpha,
    ).opts(responsive=True)
    return plot


def plot_gridded_data(ds, x="longitude", y="latitude", z="t2m", time="time", cmap="coolwarm", fix_clim=True):

    if fix_clim:
        clim = (ds[z].min(), ds[z].max())
    else:
        clim = None
    return ds.hvplot(x=x, y=y, z=z, cmap=cmap, geo=True, clim=clim)


def plot_avg_timeseries(ds, x="longitude", y="latitude", z="t2m", time="time", color="k"):
    return ds[z].mean([x, y]).hvplot.line(time, color=color)


def plot_gridded_time_slice(ds, timevar, zvar, time, clim=None):
    ds_slice = ds[zvar].sel({timevar: time})
    fig = ds_slice.hvplot.image(cmap='Greens', geo=True, rasterize=True, clim=clim)
    return fig


class GriddedPlotWithSlider(param.Parameterized):
    time_slider = param_widget(pn.widgets.DiscreteSlider(name="Datetime slider"))
    fig = param.ClassSelector(class_=pn.pane.HoloViews, default=pn.pane.HoloViews(sizing_mode="stretch_both"))

    def __init__(self, ds, timevar, zvar, clim=None, **params):
        super().__init__(**params)

        # Rename
        self.time_slider.name = "Time"

        # Options for slider
        vals = ds[timevar].values
        options = {str(val): val for val in vals}

        self.ds = ds
        self.timevar = timevar
        self.zvar = zvar
        clim=clim

        self.time_slider.options = options
        self.fig.object = plot_gridded_time_slice(self.ds, self.timevar, self.zvar, vals[0], clim=clim)
        self.fig_with_widget = pn.Column(self.fig, self.time_slider)


    @param.depends("time_slider.value_throttled", watch=True)
    def plot_slice(self):

        fig = plot_gridded_time_slice(self.ds, self.timevar, self.zvar, self.time_slider.value, self.clim)
        self.fig.object = fig