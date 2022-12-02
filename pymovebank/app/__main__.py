import panel as pn

# all registered apps need to be imported to the same folder
# as the apps dict is defined. this is because
# when the apps dict is imported, then it imports each app,
# which registers them
from pymovebank.app.apps import apps

if __name__ == "__main__":
    pn.serve(
        apps,
        port=5006
    )
