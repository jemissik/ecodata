# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
import os
import sys

sys.path.insert(0, os.path.abspath("../.."))
sys.path.insert(0, os.path.abspath("../../pymovebank/"))

# -- Project information -----------------------------------------------------

project = "PyMovebank"
copyright = "2023, Justine Missik"
author = "Justine Missik"

html_context = {
    "github_user": "jemissik",
    "github_repo": "pymovebank",
    "github_version": "develop",
    "doc_path": "docs/package",
}