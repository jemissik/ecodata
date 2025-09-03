ecodata_ver=`python print_ecodata_ver.py`
export ECODATA_VERSION=${ecodata_ver}

echo "Building with:"
echo "  ECODATA_VERSION         = $ECODATA_VERSION"
echo "  ECODATA_INSTALL_BRANCH  = $ECODATA_INSTALL_BRANCH"
echo "  REPO                    = $REPO"

constructor --config-filename construct_dev.yaml