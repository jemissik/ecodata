import os
import subprocess


def main():
    output = subprocess.run("conda search conda-forge::ecodata".split(), capture_output=True, text=True)
    lines = output.stdout.splitlines()
    lastline = lines[-1]
    version = lastline.split()[1]
    print(version)


if __name__ == "__main__":
    main()
