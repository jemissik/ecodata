# Run from pymovebank directory, not this directory
# Builds both the apps and package docs

rm -rf docs/_build
rm -rf docs/_autosummary
rm -rf docs/jupyter_execute
sphinx-build -b html docs docs/_build/package
PROJECT=apps sphinx-build -b html docs docs/_build/apps
