from multiproject.utils import get_project

import os
import sys
import json
import re
import urllib.request

sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("../ecodata/"))


# Projects sharing this configuration file
multiproject_projects = {
    "package": {},
    "apps": {}
}

# -- General configuration ---------------------------------------------------
copyright = "2023, Justine Missik"
author = "Justine Missik"


# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    "multiproject",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",  # Create neat summary tables for modules/classes/methods etc
    "sphinx.ext.napoleon",
    "sphinx.ext.todo",
    'myst_nb',
]

multiproject_projects = {
    "package": {
        "use_config_file": False,
        "config": {
            "project": "ECODATA",
        },
    },
    "apps": {
        "use_config_file": False,
        "config": {
            "project": "ECODATA Apps",
        },
    },
}

docset = get_project(multiproject_projects)

locale_dirs = [
    f"{docset}/locale/",
]

pygments_style='default'

# Use saved output in notebooks rather than executing on build
# Since the examples use large datasets not in the git repo, they need to execute locally
jupyter_execute_notebooks = "off"

autosummary_generate = True  # Turn on sphinx.ext.autosummary
add_module_names = False

source_suffix = {
    '.rst': 'restructuredtext',
    '.ipynb': 'myst-nb',
    '.md': 'myst-nb',
    '.myst': 'myst-nb',
}

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

todo_include_todos = True

# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.

html_theme = 'pydata_sphinx_theme'

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
# html_static_path = ['_static']

html_theme_options = {
    "github_url": "https://github.com/jemissik/ecodata",
    #   "show_nav_level": 4,
    "use_edit_page_button": True,
    "logo": {
        "text": "ECODATA Documentation",
    }
}

html_context = {
    # Fix the "edit on" links.
    "conf_py_path": f"/docs/{docset}/",
    "display_github": True, # Integrate GitHub
    "github_user": "jemissik", # Username
    "github_repo": "ecodata", # Repo name
    "github_version": "develop", # Version
}

html_logo = "ecodata-icon.png"




# To build table with latest release installers, we need to fetch the latest release from GitHub.
myst_enable_extensions = [
    "substitution",
]

REPO = "jemissik/ecodata"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"

_PATTERNS = {
    "win_x86_64":   re.compile(r"(?i)ecodata-.*-windows[-_]?x86_64\.exe$"),
    "mac_arm64":    re.compile(r"(?i)ecodata-.*-macosx[-_]?arm64\.pkg$"),
    "mac_x86_64":   re.compile(r"(?i)ecodata-.*-macosx[-_]?x86_64\.pkg$"),
    "linux_x86_64": re.compile(r"(?i)ecodata-.*-linux[-_]?x86_64\.sh$"),
}

def _github_request(url: str):
    req = urllib.request.Request(url)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "ecodata-docs")
    return urllib.request.urlopen(req, timeout=15)

def _fetch_latest_assets():
    try:
        with _github_request(API_URL) as r:
            data = json.load(r)
        tag = (data.get("tag_name") or data.get("name") or "latest").strip()
        found = {}
        for a in data.get("assets", []):
            name = a.get("name", "")
            url = a.get("browser_download_url", "")
            for key, pat in _PATTERNS.items():
                if pat.search(name):
                    found[key] = {"name": name, "url": url}
                    break
        return {"tag": tag, "assets": found}
    except Exception:
        return {"tag": "latest", "assets": {}}

def _mk_link(asset):
    return f"[{asset['name']}]({asset['url']})" if asset else "_Check releases page_"

rel = _fetch_latest_assets()

# Build the markdown table
table_md = f"""\
Download installers for the latest release ({rel['tag']}):

| OS             | Architecture          | Download |
|----------------|-----------------------|----------|
| Windows        | x86_64                | {_mk_link(rel['assets'].get('win_x86_64'))} |
| macOS          | arm64 (Apple Silicon) | {_mk_link(rel['assets'].get('mac_arm64'))} |
| macOS          | x86_64 (Intel)        | {_mk_link(rel['assets'].get('mac_x86_64'))} |
| Linux          | x86_64                | {_mk_link(rel['assets'].get('linux_x86_64'))} |

Use the Apple Silicon installer for M-series Macs and the Intel installer for x86_64 Macs.

"""

# 2) Inject as a substitution
myst_substitutions = globals().get("myst_substitutions", {})
myst_substitutions.update({
    "installers_table": table_md
})
