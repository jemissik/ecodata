# Installing the apps

See below for steps to install and run the ECODATA-Prepare apps. For **Mac/Linux**, you will run the commands below from the built-in Mac "Terminal" application, or any other terminal app. For **Windows**, you will install Anaconda and then run the commands below using the Anaconda Powershell Prompt. You can launch this by searching for "Anaconda Powershell" in the start menu, or by launching Anaconda Navigator and looking for "Powershell Prompt" in the home screen.

**Note:** The apps are still under development, and installation will be simplified soon!

## Installing Anaconda

1. Anaconda Distribution is an free, open-source distribution platform for managing and deploying Python and R packages. Install Anaconda if you do not have it on your computer. [Download the installer here](https://www.anaconda.com/products/distribution) and then run the file to install the program.

2. **For Windows only:** To address compatibility issues, downgrade conda to the previous version (v4.14). In the Anaconda Powershell prompt, run:

    ```bash
    conda install conda=4.14
    ```


## Preparing the Python environment and installing ECODATA-Prepare

3. [Download the conda environment file here](../../environment-clean-build.yml). This file specifies the Python package requirements used by the apps, and can be deleted following installation.

4. Open a terminal (for Windows, use Anaconda Powershell).

5. Navigate to the directory where you saved the environment file. Run ``cd <path_to_directory>`` (note that you need to replace the path with the actual path to the directory where you saved the file), for example:

    ```bash
    cd Downloads
    ```

6. You can confirm you navigated to the correct directory by running "ls" to list the files in that directory:

    ```bash
    ls
    ```
    You should see the environment file in the output, for example:

    ```bash
        Directory: C:\Users\jmissik\Downloads


    Mode                LastWriteTime       Length Name
    ----                -------------       ------ ----
    -a----      11/30/2022  12:38 PM        23470 environment-clean-build.yml
    ```

If your directory has many files, replace "ls" with "ls -lt" to sort files by the time they were last modified.

7. Install the environment (note that if your environment file is not called "environment-clean-build.yml", you need to replace this name with the actual name of your file):

    ```bash
    conda env create --file environment-clean-build.yml --name pmv
    ```

    This will download all of the packages for the application, so it may take some time to finish, depending on your internet speed. Once the environment is successfully created, the terminal output should look something like:

    ```bash
    done
    #
    # To activate this environment, use
    #
    #   $ conda activate pmv
    #
    # To deactivate an active environment, use
    #
    #   $ conda deactivate
    ```

    If there were issues with the installation, you can try using the locked environment file for your operating system instead, [available here](https://pymovebank.readthedocs.io/en/main/apps/environment_files.html)

8. After the Python environment is completed, you need to install the ECODATA-Prepare application itself. From the terminal, run:

    ```bash
    conda activate pmv
    pip install git+https://github.com/jemissik/pymovebank.git@main
    ```


## Launching ECODATA-Prepare

Launch the ECODATA-Prepare apps using the command below. For **Mac/Linux**, you will run the command below from the built-in Mac “Terminal” application, or any other terminal app. For **Windows**, you will run the command using the Anaconda Powershell Prompt. If this is not already open, find it by searching for "Anaconda Powershell" in the start menu, or by launching Anaconda Navigator and looking for "Powershell Prompt" in the home screen.

```bash
conda activate pmv
python -m pymovebank.app
```

A window will open on your default web browser, showing the main app gallery page (the apps are running locally at ``localhost:5006``). There may be a short wait the first time you launch the apps, or the first time you launch after an update.

You may receive a message "Do you want the application "python3.9" to accept incoming network connections?" You can click Allow.

Keep the terminal application you used to launch the program open while running the app. To shut down the apps, close the terminal that you used for launching.


## Updating ECODATA-Prepare

If there are new updates to the apps, you can update your version of the apps by running the command below:

```bash
conda activate pmv
pip uninstall pymovebank
pip install git+https://github.com/jemissik/pymovebank.git@main
```
