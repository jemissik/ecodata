from __future__ import annotations

import os
import pathlib
from collections import OrderedDict
from typing import AnyStr, ClassVar, Optional, Type

import panel as pn
import param
from panel.io import PeriodicCallback
from panel.layout import Column, Divider, ListPanel, Row
from panel.util import fullpath
from panel.viewable import Layoutable
from panel.widgets.input import TextInput

from ecodata.app import config


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

    header_css_classes = param.List(
        ["card-header-custom"],
        doc="""CSS classes to apply to the header element.""")


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

    header_css_classes = param.List(
        ["card-header-custom"],
        doc="""CSS classes to apply to the header element.""")


class KeyWatcher(pn.reactive.ReactiveHTML):
    value = param.Integer(default=0)
    watched = param.Parameter()
    key = param.String("Enter")

    _template = """
    <div id="wrapper">${watched}</div>
    """

    _dom_events = {'wrapper': ['keyup']}

    def _wrapper_keyup(self, e):
        try:  # Mouse click events seemingly also get passed, but don't follow the same data structure, throwing an exception when trying to grab the 'key' key
            if e.data['key'] == self.key:
                self.value += 1
        except Exception:
            pass

    def on_click(self, callback):
        return self.param.watch(callback, 'value', onlychanged=False)


class FileSelector(pn.widgets.CompositeWidget):
    """
    The `FileSelector` widget allows browsing the filesystem on the
    server and selecting one or more files in a directory.

    Reference: https://panel.holoviz.org/reference/widgets/FileSelector.html

    :Example:

    >>> FileSelector(directory='~', file_pattern='*.png')
    """

    directory = param.String(default=os.getcwd(), doc="""
        The directory to explore.""")

    file_pattern = param.String(default='*', doc="""
        A glob-like pattern to filter the files.""")

    only_files = param.Boolean(default=False, doc="""
        Whether to only allow selecting files.""")

    margin = param.Parameter(default=(5, 10, 20, 10), doc="""
        Allows to create additional space around the component. May
        be specified as a two-tuple of the form (vertical, horizontal)
        or a four-tuple (top, right, bottom, left).""")

    show_hidden = param.Boolean(default=False, doc="""
        Whether to show hidden files and directories (starting with
        a period).""")

    size = param.Integer(default=10, doc="""
        The number of options shown at once (note this is the only
        way to control the height of this widget)""")

    refresh_period = param.Integer(default=None, doc="""
        If set to non-None value indicates how frequently to refresh
        the directory contents in milliseconds.""")

    root_directory = param.String(default=None, doc="""
        If set, overrides directory parameter as the root directory
        beyond which users cannot navigate.""")

    value = param.List(default=[], doc="""
        List of selected files.""")

    _composite_type: ClassVar[Type[ListPanel]] = Column

    def __init__(self, directory: AnyStr | os.PathLike | None = None, **params):
        if directory is not None:
            params['directory'] = fullpath(directory)
        if 'root_directory' in params:
            root = params['root_directory']
            params['root_directory'] = fullpath(root)
        if params.get('width') and params.get('height') and 'sizing_mode' not in params:
            params['sizing_mode'] = None

        super().__init__(**params)

        self.error = None  # allows errors from os to be accessed outside Model

        # Set up layout
        layout = {p: getattr(self, p) for p in Layoutable.param
                  if p not in ('name', 'height', 'margin') and getattr(self, p) is not None}
        sel_layout = dict(layout, sizing_mode='stretch_both', height=None, margin=0)
        self._control_button = pn.widgets.Button(name='Select File')

        self._selector = pn.widgets.MultiSelect(size=self.size, **sel_layout)
        self._directory = TextInput(value=self.directory, margin=(5, 10), width_policy='max')
        self._nav_bar = Row(

            self._directory,
            **dict(layout, width=None, margin=0, width_policy='max')
        )
        self._composite[:] = [self._control_button]
        # self._composite[:] = [self._nav_bar, Divider(margin=0), self._selector, Divider(margin=0)]
        self.link(self._selector, size='size')

        # Set up state
        self._stack = []
        self._cwd = None
        self._position = -1
        self._update_files(True)
        self._expanded = False

        # Set up callback
        self._enter = KeyWatcher(watched=self._directory, key="Enter")
        self._enter.on_click(self._dir_change)
        self._control_button.on_click(self._update_layout)
        self.link(self._directory, directory='value')
        self._selector.param.watch(self._update_value, 'value')
        self._directory.param.watch(self._dir_change, 'value')
        self._selector.param.watch(self._select, 'value')
        self._periodic = PeriodicCallback(callback=self._refresh, period=self.refresh_period or 0)
        self.param.watch(self._update_periodic, 'refresh_period')
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

    def _update_layout(self, event: param.parameterized.Event):
        if self._expanded:
            self._control_button.name = "Select File"
            self._composite[:] = [self._control_button]
        else:
            self._control_button.name = "Close"
            self._composite[:] = [self._nav_bar, Divider(margin=0), self._selector, Divider(margin=0),
                                  self._control_button]
        self._expanded = not self._expanded

    def _update_value(self, event: param.parameterized.Event):
        value = [v for v in event.new if not self.only_files or os.path.isfile(v)]
        self._selector.value = value
        self.value = value

    def _refresh(self):
        self._update_files(refresh=True)

    def _select(self, event: param.parameterized.Event):
        if len(event.new) != 1:
            self._directory.value = self._cwd
            return

        relpath = event.new[0].replace('📁', '')
        sel = fullpath(os.path.join(self._cwd, relpath))
        if os.path.isdir(sel):
            self._directory.value = sel
            self._update_files()
            self._selector.value = []
        else:
            self._directory.value = self._cwd

    def _dir_change(self, event: param.parameterized.Event):
        path = fullpath(self._directory.value)
        if not path.startswith(self._root_directory):
            self._directory.value = self._root_directory
            return
        elif path != self._directory.value:
            self._directory.value = path
        self._update_files()

    def _update_files(
            self, event: Optional[param.parameterized.Event] = None, refresh: bool = False
    ):
        path = fullpath(self._directory.value)

        if refresh:
            path = self._cwd
        elif event is not None and (not self._stack or path != self._stack[-1]):
            self._stack.append(path)
            self._position += 1

        self._cwd = path

        selected = self.value
        try:
            dirs, files = pn.widgets.file_selector._scan_path(path, self.file_pattern)
            os_error = False
        except PermissionError as e:
            self.error = e
            dirs, files = pn.widgets.file_selector._scan_path(self._directory.value, self.file_pattern)
            os_error = True

        for s in selected:
            check = os.path.realpath(s) if os.path.islink(s) else s
            if os.path.isdir(check):
                dirs.append(s)
            elif os.path.isfile(check):
                files.append(s)

        paths = [p for p in sorted(dirs) + sorted(files)
                 if self.show_hidden or not os.path.basename(p).startswith('.')]

        paths.insert(0, "..")
        abbreviated = ["⬆️ Go Out ⬆️"]
        for f in paths:
            if f in dirs:
                abbrev = "⛔️️" if os_error else "📁"
            else:
                abbrev = ""
            abbreviated.append(abbrev + os.path.relpath(f, self._cwd))

        options = OrderedDict(zip(abbreviated, paths))
        self._selector.options = options
        self._selector.value = selected
