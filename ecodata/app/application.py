from __future__ import annotations

import inspect
from pathlib import Path
from urllib.parse import urlsplit

import param


def name_from_url(url):
    name = (urlsplit(url).path  # extract path from url (the part after .com, .org, etc
        .strip("/")  # depending on url structure can come with leading / so we remove
        .split("/")[0]  # if path is multipart, we split and only take first (if not this does no change)
        .replace("-", " ").replace("_", " ")  # replace - and _ with space
        .title())  # turn to title case
    return name


def name_from_filename(name=None):
    filename = name or Path(inspect.stack()[1].filename).stem  # file name of calling file
    return name_from_url(filename)


class Application(param.Parameterized):
    name = param.String(
        default="New Application",
        precedence=1,
        doc="""
        The name of the application""",
    )

    category = param.String(default="Dashboard", doc="A category name", precedence=1)

    description = param.String(
        regex="^.{0,150}$",
        precedence=1,
        doc="""
        A short text introduction of max 150 characters.""",
    )
    description_long = param.String(
        precedence=1,
        doc="""
        A longer description. Can contain Markdown and HTML""",
    )
    project = param.String(
        doc="""
    The name of associated project. Can be used for governance in a multi-project site""",
        precedence=1,
    )
    servable = param.String(precedence=2, doc="The path to a servable Panel application")
    url = param.String(precedence=2, doc="The url of the application.")
    thumbnail = param.String(precedence=2, doc="The url of a thumbnail of the application.")

    @classmethod
    def from_filename(cls, filename=None, **kwargs):
        filename = filename or Path(inspect.stack()[1].filename).stem  # file name of calling file
        name = name_from_filename(filename)
        url = name.replace(" ", "_").lower()
        kwargs["name"] = name
        kwargs["url"] = url
        return cls(**kwargs)
