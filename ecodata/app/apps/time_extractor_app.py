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
    
    #new code.
    # if files.value:
    #     selected_folder = files.value[0]        
    #     filenames = [str(f) for f in Path(selected_folder).glob("*.tif")]
    #     file_format_input = pn.widgets.TextInput(placeholder=os.path.basename(filenames[0]))
    
    # Output filename input
    filename_input = pn.widgets.TextInput(placeholder="Enter output filename")

    go_button = param_widget(pn.widgets.Button(name="Extract Time", button_type="primary"))
        
    # Status
    status_text = param.String("Instructions: Use the 1st fileselector to slect folder with TIF folders. All the files in the folder much be of the same format. Use the 2nd fileselector to select the folder to save the NETCDF file. \nReplaces numbers YEAR = '#' | DAY OF THE YEAR = '%' | MONTH = '&'| DAY OF MONTH = '$'. \nExample = MOD13A1.006__500m_16_days_NDVI_doy2020353_aid0001 to MOD13A1.006__500m_16_days_NDVI_doy####%%%_aid0001.")

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
        
        #new code.
        #Update file_format_input placeholder with the first file name
        self.files.param.watch(self.update_file_format_input_placeholder, "value")
        
        # Widget groups
        self.extractor_widgets = pn.Card(
            self.files, self.out_file_dir, self.file_format_input, self.filename_input, self.go_button, title="Time Extractor"
        )

        self.status = pn.pane.Alert(self.status_text)

        # View
        self.view_objects = {"extractor_widgets": 0, "status": 1}

        self.alert = pn.pane.Markdown(self.status_text)

        self.view = pn.Column(self.extractor_widgets)
    
    #new code.
    def update_file_format_input_placeholder(self, event):
        selected_folder = self.files.value[0]        
        filenames = [str(f) for f in Path(selected_folder).glob("*.tif")]
        # file_format_input = pn.widgets.TextInput(placeholder=filenames[0])
        self.file_format_input.value = os.path.basename(filenames[0])

    @try_catch()
    @param.depends("status_text", watch=True)
    def update_status_text(self):
        self.alert.object = self.status_text
    
    @try_catch()
    @param.depends("go_button.clicks", watch=True)
    def extract_time(self):
        self.status_text = "Extracting time... Instructions: Use the 1st fileselector to slect folder with TIF folders. All the files in the folder much be of the same format. Use the 2nd fileselector to select the folder to save the NETCDF file. \nReplaces numbers YEAR = '#' | DAY OF THE YEAR = '%' | MONTH = '&'| DAY OF MONTH = '$'"
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


## This is the 2nd version - functioning code,
# import logging
# import os
# from pathlib import Path

# import panel as pn
# import param
# from panel.io.loading import start_loading_spinner, stop_loading_spinner

# from ecodata.app.config import DEFAULT_TEMPLATE
# from ecodata.panel_utils import (
#     param_widget,
#     register_view,
#     try_catch,
#     rename_param_widgets,
# )
# from ecodata.app.models import FileSelector
# from ecodata.raster_utils import geotif2nc

# logger = logging.getLogger(__file__)

# class TimeExtractor(param.Parameterized):

#     # Frames directory
#     files = pn.widgets.FileSelector('~')

#     # Output directory
#     out_file_dir = pn.widgets.FileSelector('~')

#     # Format input
#     file_format_input = pn.widgets.TextInput(placeholder="Enter file format")

#     # Output filename input
#     filename_input = pn.widgets.TextInput(placeholder="Enter output filename")

#     go_button = param_widget(pn.widgets.Button(name="Extract Time", button_type="primary"))
        
#     # Status
#     status_text = param.String("Instructions: Use the 1st fileselector to slect folder with TIF folders. All the files in the folder much be of the same format. Use the 2nd fileselector to select the folder to save the NETCDF file. \nReplaces numbers YEAR = '#' | DAY OF THE YEAR = '%' | MONTH = '&'| DAY OF MONTH = '$'. \nExample = MOD13A1.006__500m_16_days_NDVI_doy2020353_aid0001 to MOD13A1.006__500m_16_days_NDVI_doy####%%%_aid0001.")

#     def __init__(self, **params):
#         super().__init__(**params)

#         # Reset names for panel widgets
#         rename_param_widgets(
#             self,
#             [
#                 "files",
#                 "out_file_dir",
#                 "file_format_input",
#                 "filename_input",
#                 "go_button",
#             ]
#         )

#         # Update file_format_input placeholder with the first file name
#         self.files.param.watch(self.update_file_format_input_placeholder, "value")

#         # Widget groups
#         self.extractor_widgets = pn.Card(
#             self.files, self.out_file_dir, self.file_format_input, self.filename_input, self.go_button, title="Time Extractor"
#         )

#         self.status = pn.pane.Alert(self.status_text)

#         # View
#         self.view_objects = {"extractor_widgets": 0, "status": 1}

#         self.alert = pn.pane.Markdown(self.status_text)

#         self.view = pn.Column(self.extractor_widgets)

#     def update_file_format_input_placeholder(self, event):
#         selected_directory = self.files.value
#         if selected_directory:
#             filenames = [str(f) for f in Path(selected_directory[0]).glob("*.tif")]
#             if filenames:
#                 self.file_format_input.value = os.path.basename(filenames[0])
#             # else:
#             #     self.file_format_input.value = ""
                
#     # full_path = "jash/desktop/ProfessorGil/actualFileName.tif"
#     # file_name = os.path.basename(full_path)

#     @try_catch()
#     @param.depends("status_text", watch=True)
#     def update_status_text(self):
#         self.alert.object = self.status_text
    
#     @try_catch()
#     @param.depends("go_button.clicks", watch=True)
#     def extract_time(self):
#         self.status_text = "Extracting time... Instructions: Use the 1st fileselector to slect folder with TIF folders. All the files in the folder much be of the same format. Use the 2nd fileselector to select the folder to save the NETCDF file. \nReplaces numbers YEAR = '#' | DAY OF THE YEAR = '%' | MONTH = '&'| DAY OF MONTH = '$'"
#         start_loading_spinner(self.view)

#         try:
#             user_input = self.file_format_input.value
#             selected_directory = self.files.value
#             selected_output_directory = self.out_file_dir.value

#             if selected_directory:
#                 try:
#                     if self.file_format_input.value and self.filename_input.value:
#                         file_output_path = os.path.join(selected_output_directory[0], f"{self.filename_input.value}.nc")
#                         geotif2nc(selected_directory[0], file_output_path, user_input)
#                     else:
#                         raise ValueError("Please provide a valid input for 'File Format' and 'File")
#                 except:
#                     raise ValueError("Directory not found.")

#         except Exception as e:
#             msg = f"Error extracting time: {e}"
#             logger.warning(msg)
#             self.status_text = msg

#         finally:
#             stop_loading_spinner(self.view)

# @register_view()
# def view():
#     extractor = TimeExtractor()
#     template = DEFAULT_TEMPLATE(
#         main=[extractor.alert, extractor.view],
#     )
#     return template

# if __name__ == "__main__":
#     pn.serve({Path(__file__).name: view})

# if __name__.startswith("bokeh"):
#     view()
