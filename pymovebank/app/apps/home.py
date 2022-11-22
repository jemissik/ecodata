"""## The Home Page of awesome-panel.org"""
# pylint: disable=wrong-import-position, ungrouped-imports, wrong-import-order
import panel as pn

from pymovebank.app.models import config

# pylint: enable=wrong-import-position, ungrouped-imports, wrong-import-order


def _add_sections():
    pn.pane.Markdown("# Home page test!").servable()


def view():
    _, template = config.extension('tabulator', url="gridded_data_explorer_app")

    template.main.append(pn.pane.Markdown("# Select an App on the Left"))
    template.servable()
    return template

if __name__ == "__main__" or __name__.startswith("bokeh"):
    config.extension(url="home", main_max_width="900px", intro_section=False)

    _add_sections()