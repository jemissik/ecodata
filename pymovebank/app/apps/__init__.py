# all registered apps need to be imported here. this is because
# when the apps dict is imported, then it imports each app,
# which registers them
apps = {}

import pymovebank.app.apps.tracks_explorer_app
import pymovebank.app.apps.gridded_data_explorer_app
import pymovebank.app.apps.subsetter_app
import pymovebank.app.apps.movie_maker_app
