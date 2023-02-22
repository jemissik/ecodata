import panel as pn

from ecodata.panel_utils import templater, register_view

def _add_sections():
    pn.pane.Markdown("# Home page test!").servable()


@register_view()
def view(app):
    return templater(app.template, main=[pn.pane.Markdown("# Select an App on the Left")])

if __name__ == "__main__":
    pn.serve({"home": view})


if __name__.startswith("bokeh"):
    view()
