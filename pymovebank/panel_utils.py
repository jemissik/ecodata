from __future__ import annotations

import param
import panel as pn
import functools
import logging
import subprocess
import os
import shlex
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Sequence, Union, TypeVar
import inspect


from functools import wraps
from pathlib import Path
from pymovebank.app.config import extension
from pymovebank.app.assets import get_link_list_html, list_links_html, menu_fast_html

from tkinter import Tk, filedialog

Servable = Union[Callable, pn.viewable.Viewable]

IS_WINDOWS = os.name == "nt"
PathLike = TypeVar("PathLike", str, os.PathLike)

links = []

logger = logging.getLogger(__file__)


# all registered apps need to be imported to pymovebank.app.apps. this is because
# when the apps dict is imported, then it imports each app,
# which registers them
applications = {}


def param_widget(panel_widget):
    """
    Wrapper utility to create a param.ClassSelector from a panel widget

    Parameters
    ----------
    panel_widget : pn.widgets.Widget
        panel widget

    Returns
    -------
    param.ClassSelector
        param.ClassSelector of the panel widget
    """
    return param.ClassSelector(class_=pn.widgets.Widget, default=panel_widget)


def select_file():
    """
    Get filepath from native os GUI file selector, using Tkinter

    Returns
    -------
    str
        filepath selected from the file dialog
    """
    root = Tk()
    root.attributes('-topmost', True)
    root.withdraw()
    f = filedialog.askopenfilename(multiple=False)

    if f:
        return f

def select_output(initial_dir=None, initial_file=None, extension=None):
    """
    Get filepath for output file from native os GUI file selector, using Tkinter

    Parameters
    ----------
    initial_dir : str, optional
        Initial directory opened in the GUI, by default None
    initial_file : str, optional
        Initial filename for the output file, by default None
    extension : str, optional
        Initial extension for the output file, by default None

    Returns
    -------
    str
        filepath for the output file
    """
    root = Tk()
    root.attributes('-topmost', True)
    root.withdraw()
    f = filedialog.asksaveasfilename(initialdir = initial_dir,
                                     initialfile=initial_file, defaultextension=extension)
    if f:
        return f


def try_catch(msg="Error... Check options and try again"):
    def inner(func):
        @functools.wraps(func)
        def tru_dec(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                logging.exception(msg)
                self.status_text = msg
        return tru_dec
    return inner


def split_shell_command(cmd: str):
    """
    split shell command for passing to python subproccess.
    This should correctly split commands like "echo 'Hello, World!'"
    to ['echo', 'Hello, World!'] (2 items) and not ['echo', "'Hello,", "World!'"] (3 items)
    It also works for posix and windows systems appropriately
    """
    return shlex.split(cmd, posix=not IS_WINDOWS)

def sanitize_filepath(filepath: str):
    """
    Sanitize filepath string using pathlib.
    Makes sure spaces, special characters, etc are escaped

    Parameters
    ----------
    filepath : str
        File path to sanitize

    Returns
    -------
    str
        Sanitized filepath
    """

    return str(Path(filepath).absolute().resolve())


def make_mp4_from_frames(frames_dir, output_file, frame_rate):
    frames_pattern = "Frame%d.png"
    temp_output_file = 'output.mp4'

    frames_dir_sanitized = Path(frames_dir).absolute().resolve()

    if Path(output_file).root == "":
        output_file = frames_dir_sanitized / output_file
    else:
        output_file = Path(sanitize_filepath(output_file))

    with cd_and_cd_back():
        print("Moving to frames directory...")
        os.chdir(frames_dir_sanitized)
        print(f'In directory: {os.getcwd()}')
        cmd = f"""ffmpeg -framerate {frame_rate} -i {frames_pattern}
        -vf pad='width=ceil(iw/2)*2:height=ceil(ih/2)*2'
        -c:v libx264 -pix_fmt yuv420p -y {temp_output_file} """

        subprocess.run(split_shell_command(cmd))
        print("ffmpeg done!")
        print(f"Target output file: {output_file}")

        shutil.move(temp_output_file, output_file)

    return output_file


def templater(
        template: pn.template.BaseTemplate,
        main: Servable | Sequence[Servable] = (),
        sidebar: Servable | Sequence[Servable] = (),
        header: Servable | Sequence[Servable] = (),
):
    main = main if isinstance(main, Sequence) else [main]
    sidebar = sidebar if isinstance(sidebar, Sequence) else [sidebar]
    header = header if isinstance(header, Sequence) else [header]
    for app in main:
        template.main.append(app)
    for app in sidebar:
        template.sidebar.append(app)
    for app in header:
        template.header.append(app)

    return template

def register_view(*ext_args, url=None, **ext_kw):
    url = url or Path(inspect.stack()[1].filename).stem  # file name of calling file
    app = extension(*ext_args, url=url, **ext_kw)
    if app.category == "Dashboard" or app.category == "Dashboards":
        print(app.name, app.url)
        link = get_link_list_html({"url": app.url, "name": app.name})
        links.append(link)
    def inner(view):
        @wraps(view)
        def wrapper(*args, **kwargs):

            template = view(app, *args, **kwargs)

            list_html = {"dashboard_links": list_links_html(links)}
            template.sidebar_footer = menu_fast_html(accent=template.accent_base_color, jinja_subs=list_html)
            return template
        app.view = wrapper
        applications[url] = app

        return wrapper
    return inner


@contextmanager
def cd_and_cd_back(path: PathLike = None):
    """Context manager that will return to the starting directory
    when the context manager exits, regardless of what directory
    changes happen between start and end.
    Parameters
    ==========
    path
        If supplied, will change directory to this path at the start of the
        context manager (it will "cd" to this path before "cd" back to the
        original directory)
    Examples
    ========
    >>> starting_dir = os.getcwd()
    ... with cd_and_cd_back():
    ...     # with do some things that change the directory
    ...     os.chdir("..")
    ... # When we exit the context manager (dedent) we go back to the starting directory
    ... ending_dir = os.getcwd()
    ... assert starting_dir == ending_dir
    >>> starting_dir = os.getcwd()
    ... path_to_change_to = ".."
    ... with cd_and_cd_back(path=path_to_change_to):
    ...     # with do some things inside the context manager
    ...     ...
    ... # When we exit the context manager (dedent) we go back to the starting directory
    ... ending_dir = os.getcwd()
    ... assert starting_dir == ending_dir
    """
    cwd = os.getcwd()
    try:
        if path:
            os.chdir(path)
        yield
    finally:
        os.chdir(cwd)
