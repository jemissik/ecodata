"""Shared configuration and functionality for apps"""

from typing import Type

import panel as pn


SITE = "Home - Movement Data Aggregator"
HOSTNAME = "localhost"

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

DEFAULT_TEMPLATE: Type[pn.template.BaseTemplate] = pn.template.FastListTemplate


def extension(
    *args,
    **kwargs,
):
    """convenience function in case we want to apply the same extension
    settings for all apps ever"""
    pn.extension(*args, **kwargs)
    return


def format_tempalte(template, name, accent_color=ACCENT, main_max_width=None):

    if isinstance(template, pn.template.BaseTemplate):
        template.site = SITE
        template.title = name
        template.site_url = "./"

        template.header_background = accent_color

        if main_max_width:
            template.main_max_width = main_max_width
        if isinstance(template, (pn.template.FastListTemplate, pn.template.FastGridTemplate)):
            template.accent_base_color = accent_color
