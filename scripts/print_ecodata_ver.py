import os
import subprocess


def main():
    output = subprocess.run("conda search conda-forge::ecodata".split(), capture_output=True, text=True)
    lines = output.stdout.splitlines()
    lastline = lines[-1]
    version1 = lastline.split()[1]
    output = subprocess.run("conda search cf-staging::ecodata".split(), capture_output=True, text=True)
    lines = output.stdout.splitlines()
    lastline = lines[-1]
    version2 = lastline.split()[1]
    if version1 >= version2:
        version = version1
    else:
        version = version2
    print(version)


if __name__ == "__main__":
    main()
