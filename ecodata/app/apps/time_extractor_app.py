import logging
import os
from pathlib import Path

import panel as pn
import param
from panel.io.loading import start_loading_spinner, stop_loading_spinner

from ecodata.app.config import DEFAULT_TEMPLATE
from ecodata.panel_utils import (
    param_widget,
    register_view,
    try_catch,
    rename_param_widgets,
)
from ecodata.app.models import FileSelector
from ecodata.raster_utils import geotif2nc

logger = logging.getLogger(__file__)

class TimeExtractor(param.Parameterized):

    # Frames directory
    files = pn.widgets.FileSelector('~')

    # Output directory
    out_file_dir =  pn.widgets.FileSelector('~')

    # Format input
    file_format_input = pn.widgets.TextInput(placeholder="Enter file format")

    # Output filename input
    filename_input = pn.widgets.TextInput(placeholder="Enter output filename")

    go_button = param_widget(pn.widgets.Button(name="Extract Time", button_type="primary"))
        
    # Status
    status_text = param.String("Ready...")

    def __init__(self, **params):
        super().__init__(**params)

        # Reset names for panel widgets
        rename_param_widgets(
            self,
            [
                "files",
                "out_file_dir",
                "file_format_input",
                "filename_input",
                "go_button",
            ]
        )

        # Widget groups
        self.extractor_widgets = pn.Card(
            self.files, self.out_file_dir, self.file_format_input, self.filename_input, self.go_button, title="Time Extractor"
        )

        self.status = pn.pane.Alert(self.status_text)

        # View
        self.view_objects = {"extractor_widgets": 0, "status": 1}

        self.alert = pn.pane.Markdown(self.status_text)

        self.view = pn.Column(self.extractor_widgets)

    @try_catch()
    @param.depends("status_text", watch=True)
    def update_status_text(self):
        self.alert.object = self.status_text
    
    @try_catch()
    @param.depends("go_button.clicks", watch=True)
    def extract_time(self):
        self.status_text = "Extracting time..."
        start_loading_spinner(self.view)

        try:
            user_input = self.file_format_input.value
            selected_directory = self.files.value
            selected_output_directory = self.out_file_dir.value

            if selected_directory:
                try:
                    if self.file_format_input.value and self.filename_input.value:
                        file_output_path = os.path.join(selected_output_directory[0], f"{self.filename_input.value}.nc")
                        geotif2nc(selected_directory[0], file_output_path, user_input)
                    else:
                        raise ValueError("Please provide a valid input for 'File Format' and 'File")
                except:
                    raise ValueError("Directory not found.")

        except Exception as e:
            msg = f"Error extracting time: {e}"
            logger.warning(msg)
            self.status_text = msg

        finally:
            stop_loading_spinner(self.view)

@register_view()
def view():
    extractor = TimeExtractor()
    template = DEFAULT_TEMPLATE(
        main=[extractor.alert, extractor.view],
    )
    return template

if __name__ == "__main__":
    pn.serve({Path(__file__).name: view})

if __name__.startswith("bokeh"):
    view()

