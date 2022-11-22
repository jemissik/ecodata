import panel as pn

from pymovebank.app.apps.tracks_explorer_app import view as tracks_explorer_app_view
from pymovebank.app.apps.gridded_data_explorer_app import view as gridded_data_explorer_app_view
from pymovebank.app.apps.subsetter_app import view as subsetter_app_view
from pymovebank.app.apps.movie_maker_app import view as movie_maker_app_view

pn.serve(
    {
        "tracks_explorer_app": tracks_explorer_app_view,
        "gridded_data_explorer_app": gridded_data_explorer_app_view,
        "subsetter_app": subsetter_app_view,
        "movie_maker_app": movie_maker_app_view,
    },
    port=5006
)
