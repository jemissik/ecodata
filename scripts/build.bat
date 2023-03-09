for /f %%i in (`python print_ecodata_ver.py`) do set ecodata_ver=%%i
export ECODATA_VERSION=${ecodata_ver}
constructor .