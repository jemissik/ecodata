# import logging
# from pathlib import Path

# import hvplot.pandas  # noqa
# import hvplot.xarray  # noqa
# import panel as pn
# import param
# from panel.io.loading import start_loading_spinner, stop_loading_spinner

# import os
# import io
# import panel as pn
# pn.extension('tabulator', 'terminal', 'ipywidgets', sizing_mode='stretch_width', loading_spinner='dots')
# from tkinter import Tk, filedialog
# import ecodata as eco
# from ecodata.raster_utils import geotif2nc

# class TimeExtractor(param.Parameterized):
#     # Create a FileSelector widget with an initial path
#     files = pn.widgets.FileSelector('~')
#     outFileDir = pn.widgets.FileSelector('~')

#     file_format_input = pn.widgets.TextInput(placeholder="Enter file format")
#     filename_input = pn.widgets.TextInput(placeholder="Enter output filename")

#     selected_directory = ""
#     selected_output_directory = ""

#     file_output_path = ""

#     # Create a button to indicate that the user has finished selecting
#     finished_button = pn.widgets.Button(name="Finished Selecting")
#     callMethodButton = pn.widgets.Button(name="Call method")

#     # Create a text pane to display the selected directory
#     selected_directory_pane = pn.pane.Str()
#     # Create a text pane to display the selected output file
#     selected_output_file_pane = pn.pane.Str()
#     fileFormat = pn.pane.Str()

#     insOne = pn.pane.Str()
#     insOne.object = "Please enter the format of your file name. User '#' as substitution block numbers realted to the time and date. Leave any '\' or '-' as is. \nExample = MOD13A1.006__500m_16_days_NDVI_doy2020353_aid0001 to MOD13A1.006__500m_16_days_NDVI_doy####%%%_aid0001. \nReplaces no. realted to year with '#' and '%' for day and '$' for time"

#     insTwo = pn.pane.Str() 
#     insTwo.object = "Select the file that contains the tif files:"

#     insThree = pn.pane.Str()
#     insThree.object = "Select the dicrectory to hold output NETCDF files:"

#     # Define a function to list all files in the selected directory
#     def list_files_in_directory(event):
#         user_input = file_format_input.value
#         selected_directory = files.value
#         selected_output_directory = outFileDir.value
#         print("Inside print_selected_directory method", flush=True)
#         selected_directory_pane.object = f"Selected directory: {selected_directory[0]}"
#         selected_output_file_pane.object = f"Selected output directory: {selected_output_directory}"
        
#         if selected_directory:
#             print("inside selected directory condition", flush=True)
#             try:
#                 if file_format_input.value:
#                     if filename_input.value:
#                         file_output_path = os.path.join(selected_output_directory[0], f"{filename_input.value}.nc") #.nc
#                         print("Inside the list_files_in_directory method")
#                         geotif2nc(selected_directory[0], file_output_path, file_format_input.value)
#                         selected_output_file_pane.object = f"File saved to: {file_output_path}"
#                     else:
#                         selected_output_file_pane.object = "Please enter a filename."
#                 else:
#                     selected_output_file_pane.object = "Please enter a file format."
#             except FileNotFoundError:
#                 selected_output_file_pane.object = "Directory not found."
#         else:
#             selected_output_file_pane.object = "No directory selected."

#     fileOut = pn.pane.Str()

#     callMethodButton.on_click(list_files_in_directory)

#     # Create a Panel dashboard with the FileSelector, Finished button, and the selected directory displayed
#     dashboard = pn.Column(insTwo, files, insThree, outFileDir, insOne, selected_directory_pane, filename_input, selected_output_file_pane, file_format_input, fileOut, selected_output_file_pane, fileFormat, callMethodButton)

#     dashboard.servable()


# @register_view()
# def view(app):
#     return templater(app.template, main=[TimeExtractor().view])

# if __name__ == "__main__":
#     pn.serve({"time_extractor_app": view})

# if __name__.startswith("bokeh"):
#     view()


import logging
from pathlib import Path

import panel as pn
import param
from panel.io.loading import start_loading_spinner, stop_loading_spinner

from ecodata.panel_utils import (
    make_mp4_from_frames,
    param_widget,
    register_view,
    templater,
    try_catch,
)
from ecodata.app.models import FileSelector

logger = logging.getLogger(__file__)


class MovieMaker(param.Parameterized):

    # Frames directory
    frames_dir = param_widget(FileSelector(constrain_path=False, expanded=True))

    # Output file
    output_file = param_widget(
        pn.widgets.TextInput(placeholder="Choose an output file...", value="output.mp4", name="Output file")
    )

    frame_rate = param_widget(
        pn.widgets.EditableIntSlider(name="Frame rate", start=1, end=30, step=1, value=1, sizing_mode="fixed")
    )

    # Go button
    go_button = param_widget(pn.widgets.Button(name="Make movie", button_type="primary", sizing_mode="fixed"))

    # Status
    status_text = param.String("Ready...")

    def __init__(self, **params):
        super().__init__(**params)

        # Reset names
        self.frames_dir.name = "Frames directory"
        self.output_file.name = "Output file"
        self.frame_rate.name = "Frame rate"
        self.go_button.name = "Make movie!"

        # Widget groups
        self.movie_widgets = pn.Card(
            self.frames_dir, self.frame_rate, self.output_file, self.go_button, title="Movie maker"
        )

        self.status = pn.pane.Alert(self.status_text)

        # View
        self.view_objects = {"movie_widgets": 0, "status": 1}

        self.alert = pn.pane.Alert(self.status_text)

        self.view = pn.Column(self.movie_widgets, self.alert)

    @try_catch()
    @param.depends("status_text", watch=True)
    def update_status_text(self):
        self.alert.object = self.status_text

    @try_catch()
    @param.depends("go_button.value", watch=True)
    def make_movie(self):

        self.status_text = "Creating movie..."
        start_loading_spinner(self.view)
        try:
            output_file = make_mp4_from_frames(
                self.frames_dir.value, self.output_file.value, frame_rate=self.frame_rate.value
            )
            assert output_file.exists()
            self.status_text = f"Movie saved to: {output_file}"

        except Exception as e:
            msg = "Error creating movie...."
            logger.warning(msg + f":\n{e!r}")
            self.status_text = msg
        finally:
            stop_loading_spinner(self.view)


@register_view()
def view(app):
    return templater(app.template, main=[MovieMaker().view])

if __name__ == "__main__":
    pn.serve({"movie_maker_app": view})

if __name__.startswith("bokeh"):
    view()
