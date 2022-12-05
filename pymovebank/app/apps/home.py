"""## The Home Page of awesome-panel.org"""
# pylint: disable=wrong-import-position, ungrouped-imports, wrong-import-order
import panel as pn

from pymovebank.panel_utils import templater, register_view

# pylint: enable=wrong-import-position, ungrouped-imports, wrong-import-order


def _add_sections():
    pn.pane.Markdown("# Home page test!").servable()


@register_view()
def view(app):
    return templater(app.template, main=[pn.pane.Markdown("# Select an App on the Left")])

if __name__ == "__main__":
    pn.serve({"home": view})


if __name__.startswith("bokeh"):
    view()
