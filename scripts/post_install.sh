. "$PREFIX/etc/profile.d/conda.sh" && conda activate "$PREFIX"
python -m pip install git+https://github.com/jemissik/ecodata@${ECODATA_INSTALL_BRANCH}
