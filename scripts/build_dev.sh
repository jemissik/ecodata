ecodata_ver=`python print_ecodata_ver.py`
export ECODATA_VERSION=${ecodata_ver}
export ECODATA_INSTALL_BRANCH=${1:-develop}
constructor --config-filename construct_dev.yaml