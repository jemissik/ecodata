"""Shared configuration and functionality for apps"""

from typing import Optional, Union
from urllib.parse import urlsplit


import panel as pn
from pymovebank.app.application import Application
from panel.template import FastGridTemplate, FastListTemplate

from pymovebank.app.assets import FAST_CSS

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

_TEMPLATES = [FastGridTemplate, FastListTemplate]

for _template in _TEMPLATES:
    _template.param.site.default = SITE
    _template.param.accent_base_color.default = ACCENT

    _template.param.header_background.default = ACCENT


def extension(
    *args,
    app: Optional[Application] = None,
    url,
    site=SITE,
    template: Optional[Union[str, pn.template.BaseTemplate]] = "fast",
    accent_color=ACCENT,
    main_max_width=None,
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

    if not app:
        name = (urlsplit(url).path  # extract path from url (the part after .com, .org, etc
                .strip("/")  # depending on url structure can come with leading / so we remove
                .split("/")[0]  # if path is multipart, we split and only take first (if not this does no change)
                .replace("-", " ").replace("_", " ")  # replace - and _ with space
                .title())  # turn to title case
        app = Application(name=name, url=url)

    if isinstance(template, pn.template.BaseTemplate):
        template.site = site
        template.title = app.name
        template.site_url = "./"

        template.header_background = accent_color

        if main_max_width:
            template.main_max_width = main_max_width
        if isinstance(template, (pn.template.FastListTemplate, pn.template.FastGridTemplate)):
            template.accent_base_color = accent_color

    # if intro_section and template not in [pn.template.FastGridTemplate]:
    #     app.intro_section().servable()

    app.template = template

    return app
