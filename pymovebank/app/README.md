**Mac/Linux**: Run the commands from any terminal app

**Windows**: Run the commands using the Anaconda Powershell Prompt. You can launch this by searching for "Anaconda powershell" in the start menu, or by launching Anaconda Navigator and looking for "Powershell Prompt" in the home screen.

## Installing the apps (using the Anaconda Navigator GUI)

**Note** The apps are still under development, and installation will be simplified soon!

1. Install Anaconda [Download installer here](https://www.anaconda.com/products/distribution)
    - **For windows:** Downgrade conda to the previous version. In the Anaconda Powershell prompt, run:

    ```commandline
    conda install conda=4.14
    ```
    
2. Download the environment file
3. Create a python environment for the app.
   - Launch "Anaconda Navigator"
   - Click on the "Environments" tab (in the sidebar on the left)
   - Click on the "Import" button at the bottom of the window.
   - In the window that pops up, click on the folder icon in the "Local drive" option. A file chooser window should open. Select the environment file that you downloaded and click "Open".
   - In the box for "New environment name", enter "pmv" as the name.
   - If you have previously installed this environment (you will see an environment with this name in the environments list), select the checkbox for "Overwrite existing environment"
   - Click "Import". This will download all of the packages for the application, so it may take some time to finish, depending on your internet speed.
4. After the python environment is completed, you need to install the aggregator app itself. From the terminal, run:

```bash
conda activate pmv
pip install git+https://github.com/jemissik/pymovebank.git@develop
```

## Installing the apps from command line (alternative method)

Alternatively, you can install the app from the command line. This may be helpful for troubleshooting if you are running into issues.

1. Open a terminal (for windows, use Anaconda Powershell)
2. Navigate to the directory where you saved the environment file. Run cd path_to_directory (note that you need to replace the path with the actual path to the directory where you saved the file):

```commandline
cd .\Desktop\
```

3. You can confirm you navigated to the correct directory by running "ls" to list the files in that directory:

```bash
ls
```
 You should see the environment file in the output, for example:
```commandline
    Directory: C:\Users\jmissik\Desktop


Mode                 LastWriteTime         Length Name
----                 -------------         ------ ----
-a----        11/23/2022   9:17 AM          23470 environment-win-lock.yml
```

4. Install the environment (note that if your environment file is not called "environment-win-lock.yml", you need to replace this name with the actual name of your file):
```commandline
conda env create --file .\environment-win-lock.yml --name pmv
```

This will download all of the packages for the application, so it may take some time to finish, depending on your internet speed. Once this process is completed, the output will look something like:
```commandline

```

5. After the python environment is completed, you need to install the aggregator app itself. From the terminal, run:

```commandline
conda activate pmv
pip install git+https://github.com/jemissik/pymovebank.git@develop
```


## Launching the apps

To open a terminal:

**Mac/Linux**: Run the commands from any terminal app (e.g., the built-in Mac application called "Terminal")

**Windows**: Run the commands using the Anaconda Powershell Prompt. You can launch this by searching for "Anaconda powershell" in the start menu, or by launching Anaconda Navigator and looking for "Powershell Prompt" in the home screen.

```bash
conda activate pmv
python -m pymovebank.app
```

A browser window will open to the main app gallery page (the apps are running locally at ``localhost:5006``). There may be a short wait the first time you launch the apps (or the first time you launch after an update).

To shut down the apps, close the terminal that you used for launching.


## Updating the apps

If there are new updates to the apps, you can update your version of the apps by running:

```bash
conda activate pmv
pip uninstall pymovebank
pip install git+https://github.com/jemissik/pymovebank.git@develop
```
