$env:ECODATA_VERSION = python print_ecodata_ver.py

Write-Host "Building with:"
Write-Host "  ECODATA_VERSION         = $env:ECODATA_VERSION"
Write-Host "  ECODATA_INSTALL_BRANCH  = $env:ECODATA_INSTALL_BRANCH"
Write-Host "  REPO                    = $env:REPO"

constructor --config-filename construct_dev.yaml