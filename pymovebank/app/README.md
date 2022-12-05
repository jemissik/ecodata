**Mac/Linux**: Run the commands from any terminal app

**Windows**: Run the commands using the Anaconda Powershell Prompt. You can launch this by searching for "Anaconda powershell" in the start menu, or by launching Anaconda Navigator and looking for "Powershell Prompt" in the home screen.

**Note:** The apps are still under development, and installation will be simplified soon!

## Installing the apps from command line

1. Install Anaconda [Download installer here](https://www.anaconda.com/products/distribution)
    - **For windows:** Downgrade conda to the previous version. In the Anaconda Powershell prompt, run:

    ```bash
    conda install conda=4.14
    ```

2. Download the environment file for your operating system [Download files here](https://jemissik.github.io/pymovebank/apps/environment_files.html).

3. Open a terminal (for windows, use Anaconda Powershell)
4. Navigate to the directory where you saved the environment file. Run ``cd <path_to_directory>`` (note that you need to replace the path with the actual path to the directory where you saved the file), for example:

    ```bash
    cd Downloads
    ```

5. You can confirm you navigated to the correct directory by running "ls" to list the files in that directory:

    ```bash
    ls
    ```
    You should see the environment file in the output, for example:

    ```bash
        Directory: C:\Users\jmissik\Downloads


    Mode                 LastWriteTime         Length Name
    ----                 -------------         ------ ----
    -a----        11/30/2022  12:38 PM          23470 environment-win-lock.yml
    ```

6. Install the environment (note that if your environment file is not called "environment-win-lock.yml", you need to replace this name with the actual name of your file):

    ```bash
    conda env create --file environment-win-lock.yml --name pmv
    ```

    This will download all of the packages for the application, so it may take some time to finish, depending on your internet speed. Once the environment is successfully created, the terminal output should look something like:

    ```bash
    done
    #
    # To activate this environment, use
    #
    #     $ conda activate pmv
    #
    # To deactivate an active environment, use
    #
    #     $ conda deactivate
    ```

    If there were issues with the installation, try using the "All platforms" environment file instead.

7. After the python environment is completed, you need to install the aggregator app itself. From the terminal, run:

    ```bash
    conda activate pmv
    pip install git+https://github.com/jemissik/pymovebank.git@main
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
pip install git+https://github.com/jemissik/pymovebank.git@main
```
