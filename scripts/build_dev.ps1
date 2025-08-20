param(
    [string]$ecodata_install_branch = "develop"
)

$env:ECODATA_INSTALL_BRANCH = $ecodata_install_branch
$env:ECODATA_VERSION = python print_ecodata_ver.py
constructor --config-filename construct_dev.yaml