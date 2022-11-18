import param
import panel as pn
import functools
import logging

from tkinter import Tk, filedialog

logger = logging.getLogger(__file__)


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
