from pathlib import Path

from setuptools import setup

BASE_ENV = Path(__file__).resolve().parent / "environment-clean-build.yml"
DEV_ENV = Path(__file__).resolve().parent / "pymovebank-dev-env.yml"


# dictionary any package renaming from conda package names to PyPI names
# ex) PyTorch is pytorch on conda, but listed as torch on PyPI
pkg_renames = {"git+https://github.com/madeline-scyphers/panel-jstree.git": "panel-jstree @ git+https://github.com/madeline-scyphers/panel-jstree.git"}


# set of any packages in your environment files you don't want to include
pkgs_to_exclude = {"ffmpeg", "nodejs>=14", "git"}


def remove_non_pkgs(pkg: str):
    is_pip = "-pip" in pkg
    is_python = "-python=" in pkg
    is_self = "--e." in pkg
    is_inline_cmt = pkg.startswith("#")
    if not is_pip and not is_python and not is_self and not is_inline_cmt:
        return True


def exclude_excluded_pkgs(pkg: str):
    if pkg not in pkgs_to_exclude:
        return True


def strip_non_pkg_chars(pkg: str):
    return (
        pkg.lstrip("-")  # remove leading dashes from listing packages
        .split("::")[-1]  # on pkgs that specify directly their channel, take pkg, not channel
        .split("#")[0]  # split inline comments and take pkg
    )


def rename_pkgs(pkg: str):
    if pkg in pkg_renames:
        return pkg_renames[pkg]
    return pkg


def process_file(path):
    with open(path, "r") as f:
        file = f.read()
    deps = file.split("dependencies:")[-1].strip().replace(" ", "").splitlines()
    deps = [dep for dep in deps if dep.startswith("-")]
    almost_pkgs = filter(remove_non_pkgs, deps)
    pkgs = map(strip_non_pkg_chars, almost_pkgs)
    pkgs = filter(exclude_excluded_pkgs, pkgs)
    pkgs = map(rename_pkgs, pkgs)
    return list(pkgs)


base_pkgs = process_file(BASE_ENV)

dev_pkgs = [pkg for pkg in process_file(DEV_ENV) if pkg not in base_pkgs]


if __name__ == '__main__':
    setup(
        install_requires=base_pkgs,
        extras_require={"dev": dev_pkgs},
    )