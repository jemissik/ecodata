All the following instructions assume your env is named pmv, change the instructions for your needs.

**Mac/Linux**: Run the commands from any terminal app

**Windows**: Run the commands using the Anaconda Powershell terminal. Search for "Anaconda powershell" in the start menu.


## Launching the apps
From your terminal, run:

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
