set -ex

source activate "${PREFIX}"


echo "

echo \"
Launching App...
\"

source activate ${PREFIX}; python -m ecodata.app
" > "${PREFIX}"/ecodata.sh
chmod +x "${PREFIX}"/ecodata.sh

cp "${PREFIX}"/ecodata.sh ~/Desktop/ecodata.sh
