source activate "${PREFIX}"

pip install git+https://github.com/jemissik/pymovebank.git@feature/conda-constructor awesome-panel-extensions==20221019.2 git+https://github.com/madeline-scyphers/panel-jstree.git

pmv_path=`python -c "import pymovebank; print(pymovebank.__path__[0])"`

echo|set /p="
source activate ${PREFIX}

#python -mwebbrowser http://localhost:5006/home

python -m panel serve ${pmv_path}/apps/apps/*.py
" > "${PREFIX}"/app.bat
