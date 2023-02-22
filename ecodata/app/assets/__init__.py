"""Module containing paths to and text of html files"""
from __future__ import annotations

import pathlib
from typing import Union

import jinja2
import panel as pn

PATH = pathlib.Path(__file__).parent

MAIN_MENU = (PATH / "main_menu.html").read_text(encoding="utf8")
LIST_ITEM_TEMPLATE = (PATH / "list_item_template.html").read_text(encoding="utf8")
FAST_CSS_PATH = PATH / "fast.css"
FAST_CSS = (PATH / "fast.css").read_text(encoding="utf8")

_JINJA_ENV = jinja2.Environment(
    loader=jinja2.BaseLoader(),
)
LINKS_TEMPLATE = _JINJA_ENV.from_string(LIST_ITEM_TEMPLATE)

pn.state.cache["cached"] = {}

CACHE = pn.state.cache["cached"]


def menu_fast_html(accent: str = "#1f77b4", jinja_subs = None) -> str:
    """Combines the specific app_html to other html into a fast html menu"""

    menu = MAIN_MENU.replace("#1f77b4".format(), accent)
    if jinja_subs:
        if isinstance(jinja_subs, dict):
            jinja_subs = [jinja_subs]
        for sub in jinja_subs:
            template = _JINJA_ENV.from_string(menu)
            menu = template.render(**sub)
    return menu


def list_links_html(links: list[Union[dict, str]]):
    list_html = "\n".join(get_link_list_html(link) if isinstance(link, dict) else link for link in links)
    list_html = f"<ul>\n{list_html}\n</ul>"
    return list_html


def get_link_list_html(link: dict):
    return LINKS_TEMPLATE.render(**link)