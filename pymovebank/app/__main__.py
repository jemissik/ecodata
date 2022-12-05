import panel as pn

# all registered apps need to be imported to the same folder
# as the applications is imported from. this is because
# when the apps dict is imported, then it imports each app,
# which registers them
from pymovebank.app.apps import applications

if __name__ == "__main__":
    pn.serve(
        applications,
        port=5006
    )
