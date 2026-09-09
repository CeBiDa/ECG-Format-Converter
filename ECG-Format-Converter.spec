# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ECG-Format-Converter standalone builds."""

import sys

block_cipher = None

datas = [
    ("config.ini", "."),
]

hidden_imports = [
    "matplotlib.backends.backend_qtagg",
    "matplotlib.font_manager",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtCore",
    "scipy.interpolate",
    "scipy.signal",
    "scipy.ndimage",
    "scipy.io",
    "pydicom",
    "pywt",
    "PyEMD",
    "wfdb",
    "lxml",
    "lxml.etree",
]

a = Analysis(
    ["ui.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "torch", "IPython"],
    noarchive=False,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name="ECG-Format-Converter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    exclude_binaries=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="ECG-Format-Converter",
    strip=False,
    upx=True,
    upx_exclude=[],
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="ECG-Format-Converter.app",
        icon=None,
        bundle_identifier="io.github.cebida.ecg-format-converter",
        info_plist={"NSHighResolutionCapable": True},
    )
