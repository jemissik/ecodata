set -ex

source activate "${PREFIX}"


echo "

echo \"
Launching App...
\"

source activate ${PREFIX}; python -m ecodata.app
" > "${PREFIX}"/ecodata.sh
chmod +x "${PREFIX}"/ecodata.sh

cp "${PREFIX}"/ecodata.sh ~/Downloads/ecodata.sh


echo "

echo \"
Updating ECODATA
\"

source activate ${PREFIX}; conda update ecodata

" > "${PREFIX}"/update_ecodata.sh
chmod +x "${PREFIX}"/update_ecodata.sh