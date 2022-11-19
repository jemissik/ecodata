set -ex

touch ~/test.txt

echo 'installing form github'

#source "$PREFIX/etc/profile.d/conda.sh" &&
conda activate "$PREFIX"

which python
pip install git+https://github.com/jemissik/pymovebank.git awesome-panel-extensions==20221019.2