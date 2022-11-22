set -ex

source activate "${PREFIX}"

pip install git+https://github.com/jemissik/pymovebank.git@feature/gui-move-to-multi-page-app awesome-panel-extensions==20221019.2 git+https://github.com/madeline-scyphers/panel-jstree.git

pmv_path=`python -c "import pymovebank; print(pymovebank.__path__[0])"`

echo "
source activate ${PREFIX}

#python -mwebbrowser http://localhost:5006/home

python -m panel serve ${pmv_path}/apps/apps/*.py
" > "${PREFIX}"/app.sh
chmod +x "${PREFIX}"/app.sh
