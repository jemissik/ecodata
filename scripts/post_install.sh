set -ex


echo "

echo \"
Launching App...
\"

source activate ${PREFIX}; python -m ecodata.app
" > "${PREFIX}"/ecodata.command
chmod +x "${PREFIX}"/ecodata.command

cp "${PREFIX}"/ecodata.command ~/Downloads/ecodata.command


echo "

echo \"
Updating ECODATA
\"

source activate ${PREFIX}; conda update ecodata

" > "${PREFIX}"/update_ecodata.command
chmod +x "${PREFIX}"/update_ecodata.command