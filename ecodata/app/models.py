from __future__ import annotations

import os
from typing import AnyStr, ClassVar, Type
from pathlib import Path

import panel as pn
import param
import webbrowser
from panel.util import fullpath

from ecodata.app import config
from ecodata.panel_utils import param_widget

from distributed.dashboard.components.scheduler import TaskProgress


class SimpleDashboardCard(param.Parameterized):

    open_dashboard_button = param_widget(
        pn.widgets.Button(button_type="primary", name="Open processing dashboard", align="start", sizing_mode="fixed"))
    def __init__(self, dask_client, **params):
        super().__init__(**params)

        # Reset names for panel widgets
        self.open_dashboard_button.name = "Open processing dashboard"


        self.dask_client = dask_client
        self.dash_address = self.dask_client.dashboard_link
        print("Dash address:")
        print(self.dash_address)

        # dashboard_description = pn.pane.HTML(
        #     f'Processing dashboard is running at: <a apperance="stealth" href={self.dash_address} target="_blank">{self.dash_address} </a>'
        #     )

        dashboard_description = pn.pane.Markdown("Processing details (progress, memory usage, etc can be viewed in the "
                                                 "processing dashboard)")

        self.dask_processing_card = pn.Card(dashboard_description, self.open_dashboard_button, title="Processing info")

    @param.depends("open_dashboard_button.value", watch=True)
    def open_full_dashboard(self):
        try:
            webbrowser.open(self.dash_address)
        except Exception as e:
            print("failed to open dashboard")

class DaskDashboardCard():
    """
    WIP dashboard card with progress plot embedded.
    Not working yet.
    """

    open_dashboard_button = param_widget(
        pn.widgets.Toggle(button_type="primary", name="Open processing dashboard", align="end", sizing_mode="fixed"))
    def __init__(self, dask_cluster, **params):
        super().__init__(**params)

        # Reset names for panel widgets
        self.open_dashboard_button.name = "Open processing dashboard"


        self.dask_cluster = dask_cluster
        # self.dash_address = self.dask_client.dashboard_link
        self.dash_address = self.dask_cluster.dashboard_link
        print("Dash address:")
        print(self.dash_address)

        self.scheduler = self.dask_cluster.scheduler
        self.progress_figure = self.get_progress_figure()

        self.dask_processing_card = pn.Card(self.open_dashboard_button, self.progress_figure, title="Processing info")

    def get_progress_figure(self):
        dask_progress = TaskProgress(self.scheduler)
        # dask_progress = SystemMonitor(self.scheduler)

        # Add periodic callback to figure
        pn.state.add_periodic_callback(dask_progress.update, 100)
        return pn.pane.Bokeh(dask_progress.root)

    @param.depends("open_dashboard_button.value", watch=True)
    def open_full_dashboard(self):
        webbrowser.open(self.dash_address)



class PMVCard(pn.Card):
    active_header_background = param.String(
        default=config.ACCENT,
        doc="""
        A valid CSS color for the header background when not collapsed.""",
    )

    header_background = param.String(
        default=config.ACCENT,
        doc="""
        A valid CSS color for the header background.""",
    )

    header_color = param.String(
        default="white",
        doc="""
        A valid CSS color to apply to the header text.""",
    )

    header_css_classes = param.List(["card-header-custom"], doc="""CSS classes to apply to the header element.""")


class PMVCardDark(pn.Card):
    active_header_background = param.String(
        default=config.PALETTE[7],
        doc="""
        A valid CSS color for the header background when not collapsed.""",
    )

    header_background = param.String(
        default=config.PALETTE[7],
        doc="""
        A valid CSS color for the header background.""",
    )

    header_color = param.String(
        default="white",
        doc="""
        A valid CSS color to apply to the header text.""",
    )

    header_css_classes = param.List(["card-header-custom"], doc="""CSS classes to apply to the header element.""")


class KeyWatcher(pn.reactive.ReactiveHTML):
    value = param.Integer(default=0)
    watched = param.Parameter()
    key = param.String("Enter")

    _template = """
    <div id="wrapper">${watched}</div>
    """

    _dom_events = {"wrapper": ["keyup"]}

    def _wrapper_keyup(self, e):
        # Mouse click events seemingly also get passed, but don't follow the same data structure,
        # throwing an exception when trying to grab the 'key' key
        try:
            if e.data["key"] == self.key:
                self.value += 1
        except Exception:
            pass

    def on_click(self, callback):
        return self.param.watch(callback, "value", onlychanged=False)


class FileSelector(pn.widgets.CompositeWidget):
    """
    The `FileSelector` widget allows browsing the filesystem on the
    server and selecting one or more files in a directory.

    Reference: https://panel.holoviz.org/reference/widgets/FileSelector.html

    :Example:

    >>> FileSelector(directory='~', file_pattern='*.png')
    """

    directory = param.String(
        default=None,
        doc="""
        The directory to explore.""",
    )

    file_pattern = param.String(
        default="*",
        doc="""
        A glob-like pattern to filter the files.""",
    )

    only_files = param.Boolean(
        default=False,
        doc="""
        Whether to only allow selecting files.""",
    )

    margin = param.Parameter(
        default=(5, 10, 20, 10),
        doc="""
        Allows to create additional space around the component. May
        be specified as a two-tuple of the form (vertical, horizontal)
        or a four-tuple (top, right, bottom, left).""",
    )

    show_hidden = param.Boolean(
        default=False,
        doc="""
        Whether to show hidden files and directories (starting with
        a period).""",
    )

    size = param.Integer(
        default=10,
        doc="""
        The number of options shown at once (note this is the only
        way to control the height of this widget)""",
    )

    refresh_period = param.Integer(
        default=None,
        doc="""
        If set to non-None value indicates how frequently to refresh
        the directory contents in milliseconds.""",
    )

    constrain_path = param.Boolean(
        default=False,
        doc="""
        If True, will constrain the user to only go to subdirectories of directory
        or root_directory (if set).
        """
    )

    root_directory = param.String(
        default=None,
        doc="""
        If set and constrain_path is True, overrides directory parameter as the root
        directory beyond which users cannot navigate.""",
    )

    expanded = param.Boolean(
        default=None,
        allow_None=True,
        doc="""
        If the file selector is embedded in an expandable layout and expanded (True),
        not expanded (False), or not embedded (None)
        """,
    )

    _composite_type: ClassVar[Type[pn.layout.ListPanel]] = pn.Column

    @property
    def value(self):
        """Value of selected file or directory"""
        return self._directory.value

    @value.setter
    def value(self, value):
        self._directory.value = value

    def __init__(self, directory: AnyStr | os.PathLike | None = None, **params):
        if directory is None:
            try:
                directory = Path.home()
                os.path.relpath(directory, os.getcwd())
            except (OSError, ValueError):
                directory = os.getcwd()
        params["directory"] = pn.util.fullpath(directory)
        if "root_directory" in params:
            root = params["root_directory"]
            params["root_directory"] = pn.util.fullpath(root)
        if params.get("width") and params.get("height") and "sizing_mode" not in params:
            params["sizing_mode"] = None

        super().__init__(**params)

        self.error = None  # allows errors from os to be accessed outside Model

        # Set up layout
        layout = {
            p: getattr(self, p)
            for p in pn.viewable.Layoutable.param
            if p not in ("name", "height", "margin") and getattr(self, p) is not None
        }
        sel_layout = dict(layout, sizing_mode="stretch_width", )
        self._control_button = pn.widgets.Button(name="Select File")

        self._selector = pn.widgets.MultiSelect(size=self.size, **sel_layout)
        self._directory = pn.widgets.TextInput(value=self.directory, margin=(5, 10), width_policy="max")
        self._nav_bar = pn.Row(self._directory, **dict(layout, width=None, margin=0, width_policy="max"))
        self.link(self._selector, size="size")

        # Set up state
        self._stack = []
        self._cwd = None
        self._position = -1
        self._update_files(refresh=True)
        self._update_layout()


        # Set up callback
        self._control_button.on_click(self._update_layout)
        self.link(self._directory, directory="value")
        self._selector.param.watch(self._update_value, "value")
        self._directory.param.watch(self._dir_change, "value")
        self._selector.param.watch(self._select, "value")
        self._periodic = pn.io.PeriodicCallback(callback=self._refresh, period=self.refresh_period or 0)
        self.param.watch(self._update_periodic, "refresh_period")
        self.param.watch(self._update_layout, "expanded")
        if self.refresh_period:
            self._periodic.start()

    def _update_periodic(self, event: param.parameterized.Event):
        if event.new:
            self._periodic.period = event.new
            if not self._periodic.running:
                self._periodic.start()
        elif self._periodic.running:
            self._periodic.stop()

    @property
    def _root_directory(self):
        return self.root_directory or self.directory

    def _update_layout(self, event: param.parameterized.Event = None):
        if self.expanded is not None:
            # this func fires on expanded changing, so we don't want this change to be picked up
            with param.parameterized.discard_events(self):
                self.expanded = not self.expanded

        if self.expanded:
            self._control_button.name = "Select File"
            self._composite[:] = [
                self._nav_bar,
                self._control_button,
            ]
        elif self.expanded is False:
            self._control_button.name = "Close"
            self._composite[:] = [
                self._nav_bar,
                pn.layout.Divider(margin=0),
                self._selector,
                pn.layout.Divider(margin=0),
                self._control_button,
            ]
        else:
            self._composite[:] = [
                self._nav_bar,
                pn.layout.Divider(margin=0),
                self._selector,
                pn.layout.Divider(margin=0),
            ]

    def _update_value(self, event: param.parameterized.Event):
        value = [v for v in event.new if not self.only_files or os.path.isfile(v)]
        self._selector.value = value
        self._value = value

    def _refresh(self):
        self._update_files(refresh=True)

    def _select(self, event: param.parameterized.Event):
        if len(event.new) == 0:
            self._directory.value = self._cwd
            return

        relpath = event.new[0].replace("📁", "")
        sel = pn.util.fullpath(os.path.join(self._cwd, relpath))
        if os.path.isdir(sel):
            self._directory.value = sel
            self._update_files()
            self._selector.value = []
        else:
            self._directory.value = sel

    def _dir_change(self, event: param.parameterized.Event):
        path = pn.util.fullpath(self._directory.value)
        if self.constrain_path and not path.startswith(self._root_directory):
            self._directory.value = self._root_directory
            return
        elif path != self._directory.value:
            self._directory.value = path
        self._update_files()


    def _update_files(self, refresh: bool = False):

        path = pn.util.fullpath(self._directory.value)
        if not os.path.isdir(path):
            path = str(Path(path).parent)

        if (not self._stack or path != self._stack[-1]):
            self._stack.append(path)
            self._position += 1

        self._cwd = path

        selected = self.value

        dirs, files = self._get_paths(path)

        for s in selected:
            check = os.path.realpath(s) if os.path.islink(s) else s
            if os.path.isdir(check):
                dirs.append(s)
            elif os.path.isfile(check):
                files.append(s)

        paths = []
        for p in sorted(dirs) + sorted(files):
            if os.path.relpath(p, self._cwd).startswith("."):
                continue
            elif os.path.basename(p).startswith(".") and self.show_hidden:
                paths.append(p)
            else:
                paths.append(p)

        options = {"⬆️..": ".."}

        for f in paths:
            if f in dirs:
                options[f"📁{os.path.relpath(f, self._cwd)}"] = f
            else:
                options[os.path.relpath(f, self._cwd)] = f

        self._selector.options = options

    def _get_paths(self, directory):
        dirs_, files = pn.widgets.file_selector._scan_path(directory, self.file_pattern)
        dirs = []
        for d in dirs_:
            try:
                if os.listdir(d):
                    dirs.append(d)
            except OSError:  # we can't access that folder
                pass
        return dirs, files
