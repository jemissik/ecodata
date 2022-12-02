"""Shared configuration and functionality for awesome_panel apps"""
from functools import wraps
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlsplit
import inspect

import panel as pn
from awesome_panel_extensions.site.gallery import GalleryTemplate
from awesome_panel_extensions.site.models import Application
from panel.template import FastGridTemplate, FastListTemplate, GoldenTemplate

from pymovebank.app.assets import menu_fast_html, list_links_html, APPLICATIONS_CONFIG_PATH, FAST_CSS, FAST_CSS_PATH
from pymovebank.app.apps import apps

SITE = "Movement Data Aggregator"

ACCENT = "#1f77b4"  # "#E1477E"
PALETTE = [
    ACCENT,
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
]

# pylint: disable=line-too-long
FAVICON = "https://raw.githubusercontent.com/awesome-panel/awesome-panel-assets/320297ccb92773da099f6b97d267cc0433b67c23/favicon/ap-1f77b4.ico"
# pylint: enable=line-too-long


APPLICATIONS = Application.read(APPLICATIONS_CONFIG_PATH)
APPLICATIONS_MAP = {app.url: app for app in APPLICATIONS}


list_html = {"dashboard_links": list_links_html([{"url": app.url, "name": app.name}
                                                 for app in APPLICATIONS if app.category == "Dashboards"])}
_TEMPLATES = [FastGridTemplate, FastListTemplate, GalleryTemplate]

# pylint: disable=line-too-long
FOLLOW_ON_TWITTER = """[![Follow on Twitter](https://img.shields.io/twitter/follow/MarcSkovMadsen.svg?style=social)](https://twitter.com/MarcSkovMadsen)"""
GITHUB_STARS = "[![GitHub stars](https://img.shields.io/github/stars/MarcSkovMadsen/awesome-panel.svg?style=social&label=Star&maxAge=2592000)](https://github.com/awesome-panel/awesome-panel/stargazers/)"
# pylint: enable=line-too-long


def get_header():
    """Returns a component to be added to the template header"""
    return pn.Row(
        pn.layout.Spacer(sizing_mode="stretch_width"),
        pn.pane.Markdown(GITHUB_STARS, sizing_mode="fixed", width=75),
        pn.pane.Markdown(FOLLOW_ON_TWITTER, sizing_mode="fixed", width=230),
        pn.layout.VSpacer(width=4),
        height=86,
        sizing_mode="stretch_width",
    )


def add_header(template: pn.template.BaseTemplate):
    """Adds a component to the header"""
    pass
    # template.header.append(get_header())


for _template in _TEMPLATES:
    _template.param.favicon.default = FAVICON
    _template.param.site.default = "Awesome Panel"
    _template.param.accent_base_color.default = ACCENT

    if not _template == GalleryTemplate:
        _template.param.header_background.default = ACCENT
        _template.param.sidebar_footer.default = menu_fast_html(accent=ACCENT, jinja_subs=list_html)


# all registered apps need to be imported to the same folder
# as the apps dict is defined. this is because
# when the apps dict is imported, then it imports each app,
# which registers them
def register_view(*ext_args, url=None, **ext_kw):
    url = url or Path(inspect.stack()[1].filename).stem  # file name of calling file
    def inner(view):
        @wraps(view)
        def wrapped_app(*args, **kwargs):
            app = extension(url=url, *ext_args, **ext_kw)

            return view(app, *args, **kwargs)
        apps[url] = wrapped_app
        return wrapped_app
    return inner


def extension(
    *args,
    url,
    site=SITE,
    template: Optional[Union[str, pn.template.BaseTemplate]] = "fast",
    # template: Optional[Union[str, pn.template.BaseTemplate]] = pn.template.FastGridTemplate,
    # template: Optional[Union[str, pn.template.BaseTemplate]] = "fastgrid",
    # template: Optional[Union[str, pn.template.BaseTemplate]] = "bootstrap",
    accent_color=ACCENT,
    main_max_width=None,
    intro_section=True,
    favicon=FAVICON,
    sizing_mode="stretch_width",
    raw_css=(FAST_CSS,),
    **kwargs,
) -> Application:
    """A customized version of pn.extension for this site"""
    # template = pn.template.FastGridTemplate(
    #     theme_toggle=False,
    #     prevent_collision=True,
    #     save_layout=True,
    #     # pylint: disable=line-too-long
    # )
    raw_css = list(raw_css) if raw_css else []
    if isinstance(template, str) or not template:
        pn.extension(*args, sizing_mode=sizing_mode, template=template, raw_css=raw_css, **kwargs)
        if template:
            template = pn.state.template
    else:
        pn.extension(*args, sizing_mode=sizing_mode, raw_css=raw_css, **kwargs)

    if url in APPLICATIONS_MAP:
        app = APPLICATIONS_MAP[url]
    else:
        name = (urlsplit(url).path  # extract path from url (the part after .com, .org, etc
                .strip("/")  # depending on url structure can come with leading / so we remove
                .split("/")[0]  # if path is multipart, we split and only take first (if not this does no change)
                .replace("-", " ").replace("_", " ")  # replace - and _ with space
                .title())  # turn to title case
        app = Application(name=name, author=None)

    if isinstance(template, pn.template.BaseTemplate):
        template.site = site
        template.favicon = favicon
        template.title = app.name
        template.site_url = "./"

        template.header_background = accent_color

        if main_max_width:
            template.main_max_width = main_max_width
        if isinstance(template, (pn.template.FastListTemplate, pn.template.FastGridTemplate)):
            template.accent_base_color = accent_color
            template.sidebar_footer = menu_fast_html(accent=accent_color, jinja_subs=list_html)
            add_header(template)

    # if intro_section and template not in [pn.template.FastGridTemplate]:
    #     app.intro_section().servable()

    app.template = template

    return app
