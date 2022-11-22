All the following instructions assume your env is name pmv, change the instructions for your needs.


## Unix
to launch apps in bash, you can use:

```bash
conda activate pmv

pmv_path=`python -c "import pymovebank; print(pymovebank.__path__[0])"`

python -m panel serve pymovebank/apps/apps/*app.py --glob --port 5006
```

## Windows

on windows you can run this command with an installed environment to get the directory of the apps and then serve them:
```commandline
conda activate pmv

for /f %%i in ('python -c "import pymovebank; print(pymovebank.__path__[0])"') do set pmv_path=%i

python -m panel serve %pmv_path%/apps/apps/*app.py --glob --port 5006
```

## Web address
Then you can open this address in your browser to open the app:

``localhost:5006``