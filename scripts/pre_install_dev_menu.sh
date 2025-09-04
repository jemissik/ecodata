set -x

menudir=$(echo "${PREFIX}"/pkgs/ecodata-menu*/Menu | awk '{print $1}')
if [ -d "$menudir" ] && [ -f "$menudir/ecodata-menu-prerelease.json" ]; then
  mv "$menudir/ecodata-menu-prerelease.json" "$menudir/ecodata-menu.json"
  echo "Renamed prerelease menu file in $menudir"
else
  echo "Menu directory or prerelease JSON not found" >&2
fi