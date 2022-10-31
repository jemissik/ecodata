import param
import panel as pn
from tkinter import Tk, filedialog


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

def detect_varnames(ds):
    matched_vars = dict(timevar = None,
                    latvar = None,
                    lonvar = None)

    label_options = dict(time_options = ['time', 'timestamp', 'Time'],
                        lat_options = ['lat', 'latitude', 'Latitude'],
                        lon_options = ['lon', 'long', 'longitude', 'Longitude'])

    # Variables in dataset
    dataset_vars = set(list(ds.coords) + list(ds))
    unmatched_vars = set(dataset_vars)

    for variable, label_opt in zip(matched_vars, label_options):
        matched_var = dataset_vars.intersection(label_options[label_opt])
        if matched_var:
            matched_var = matched_var.pop()
            matched_vars[variable] = matched_var
            unmatched_vars.remove(matched_var)
    return matched_vars, dataset_vars, unmatched_vars