# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for PaperScraper
#
# Build commands:
#   macOS:   pyinstaller PaperScraper.spec
#   Windows: pyinstaller PaperScraper.spec
#
# Output will be in dist/PaperScraper(.exe on Windows)

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect all data files needed by paperscraper (config yamls, etc.)
paperscraper_datas = collect_data_files("paperscraper")
customtkinter_datas = collect_data_files("customtkinter")

all_datas = paperscraper_datas + customtkinter_datas

# Hidden imports that PyInstaller might miss
hidden_imports = (
    collect_submodules("paperscraper")
    + collect_submodules("customtkinter")
    + [
        "openpyxl",
        "openpyxl.styles",
        "openpyxl.utils",
        "requests",
        "sqlite3",
        "PIL",
        "PIL.Image",
        "PIL.ImageTk",
        "tkinter",
        "tkinter.filedialog",
        "xml.etree.ElementTree",
        "email",
        "email.mime",
    ]
)

a = Analysis(
    ["PaperScraper_UI.py"],
    pathex=["."],
    binaries=[],
    datas=all_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "IPython",
        "jupyter",
        "notebook",
    ],
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
    name="PaperScraper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,   # No terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="assets/icon.icns",  # Uncomment and add icon file if desired
)

# macOS .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="PaperScraper.app",
        icon=None,  # Replace with "assets/icon.icns" if you have one
        bundle_identifier="com.paperscraper.app",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "1.0.0",
        },
    )
