set -ex

source activate "${PREFIX}"

echo "
source activate ${PREFIX}; python -m pymovebank.app
" > "${PREFIX}"/app.sh
chmod +x "${PREFIX}"/app.sh
