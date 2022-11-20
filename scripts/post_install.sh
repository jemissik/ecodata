set -ex

echo '## Hello from Post_install script ' > $HOME/postinstall.txt

echo 'installing form github'

source activate "$PREFIX"

which python
pip install git+https://github.com/jemissik/pymovebank.git@feature/gui-move-to-multi-page-app awesome-panel-extensions==20221019.2


pmv_path=`python -c "import pymovebank; print(pymovebank.__path__[0])"`
echo

echo "
  source activate ${PREFIX}
  python -m panel serve ${pmv_path}/apps/apps/*.py
  " > "$PREFIX"/app.sh
