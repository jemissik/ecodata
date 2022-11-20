call "%PREFIX%\Scripts\activate.bat"

pip install git+https://github.com/jemissik/pymovebank.git@feature/conda-constructor awesome-panel-extensions==20221019.2

for /f %%i in ('python -c "import pymovebank; print(pymovebank.__path__[0])"') do set pmv_path=%%i

echo|set /p="
call %PREFIX%\Scripts\activate.bat

#python -mwebbrowser http://localhost:5006/home

python -m panel serve %pmv_path%/apps/apps/*app.py --glob --port 5006
" > "${PREFIX}"/app.bat
