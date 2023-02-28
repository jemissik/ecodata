import hvplot.pandas  # noqa
import hvplot.xarray  # noqa

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
