set -ex

#source activate "${PREFIX}"

echo "

echo \"
Launching App...
\"

source activate ${PREFIX}; python -m ecodata.app
" > "${PREFIX}"/app.sh
chmod +x "${PREFIX}"/ecodata.sh
