import ecodata as eco
import click
from pathlib import Path


@click.command()
@click.option(
    "--filename",
    type=click.Path(exists=True, path_type=Path),
    help="Path to data file to subset",
)
@click.option(
    "--bbox",
    type=(float, float, float, float),
    default=None,
    help=(
        "Optional: Bounding box coordinates for the subset. Should be specified in the format: --bbox long_min lat_min"
        " long_max lat_max"
    ),
)
@click.option(
    "--track_points",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Optional: Path to csv file with animal track points.",
)
@click.option(
    "--bounding_geom",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Optional: Path to shapefile with bounding geometry",
)
@click.option(
    "--boundary_type",
    type=str,
    default="rectangular",
    help=(
        "Optional: Specifies whether the bounding shape should be rectangular (boundary_type= 'rectangular') or convex"
        " hull(boundary_type = 'convex_hull'), by default ‘rectangular’"
    ),
)
@click.option(
    "--buffer",
    type=float,
    default=0,
    help=(
        "Optional: Buffer size around the track points or bounding geometry, relative to the extent of the track points"
        " or bounding geometry. By default 0."
    ),
)
@click.option(
    "--clip",
    type=bool,
    default=False,
    help=(
        "Optional: Whether or not to clip the subsetted data to the specified boundary (i.e., cut off intersected"
        " features at the boundary edge). By default False."
    ),
)
@click.option(
    "--outfile",
    type=click.Path(exists=False, dir_okay=False, path_type=Path),
    default="output.shp",
    help="Path to write the subsetted .shp file",
)
def main(filename, bbox, track_points, bounding_geom, boundary_type, buffer, clip, outfile):

    print("Creating subset...")
    roads_subset, boundary = eco.subset_data(
        filename,
        bbox=bbox,
        track_points=track_points,
        bounding_geom=bounding_geom,
        boundary_type=boundary_type,
        buffer=buffer,
        clip=clip,
        outfile=outfile,
    )
    print(f"Subset saved to: {outfile}")


if __name__ == "__main__":
    main()
