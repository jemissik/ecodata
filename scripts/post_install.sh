set -ex

source activate "${PREFIX}"

pip install git+https://github.com/jemissik/pymovebank.git@develop awesome-panel-extensions==20221019.2

echo "
source activate ${PREFIX}; python -m pymovebank.app
" > "${PREFIX}"/app.sh
chmod +x "${PREFIX}"/app.sh
