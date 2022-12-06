# Run from pymovebank directory, not this directory

rm -rf docs/_build
rm -rf docs/_autosummary
rm -rf docs/jupyter_execute
sphinx-build -b html docs docs/_build