"""## The Home Page of awesome-panel.org"""
# pylint: disable=wrong-import-position, ungrouped-imports, wrong-import-order
import panel as pn

from pymovebank.apps.models import config

# pylint: enable=wrong-import-position, ungrouped-imports, wrong-import-order


def _add_sections():
    pn.pane.Markdown("# Home page test!").servable()


if __name__ == "__main__" or __name__.startswith("bokeh"):
    config.extension(url="home", main_max_width="900px", intro_section=False)

    _add_sections()