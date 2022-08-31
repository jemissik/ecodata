# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_data_files # this is very helpful

env_path = os.environ['CONDA_PREFIX']
bins = os.path.join(env_path, 'lib')

block_cipher = None

binaries = [
    (os.path.join(bins,'libgeos.dylib'), ''),
    (os.path.join(bins,'libgeos_c.dylib'), ''),
]

hidden_imports = [
    'ctypes',
    'ctypes.util',
    'fiona',
    'gdal',
    'geos',
    'shapely',
    'shapely.geometry',
    'shapely.geos',
    'pyproj',
    'rtree',
    'geopandas.datasets',
    'pytest',
    'pandas._libs.tslibs.timedeltas',
]

a = Analysis(
    ['subset_script.py'],
    pathex=[],
    binaries=[],
    datas=collect_data_files('geopandas', subdir='datasets'), #this is the important bit for your particular error message
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='subset_script',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
