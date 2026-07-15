# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for MarkItDown GUI.
# Build with:
#   pyinstaller --noconfirm packaging/markitdown_gui.spec

import os
from pathlib import Path

block_cipher = None

PROJECT_ROOT = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(PROJECT_ROOT / "app" / "__main__.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        # Bundle SVG icons used by the custom title bar.
        (str(PROJECT_ROOT / "app" / "assets"), str(Path("app") / "assets")),
    ],
    hiddenimports=[
        "markitdown",
        # markitdown.converters.* plugins are loaded dynamically, list them all.
        "markitdown.converters",
        "markitdown.converters.pdf",
        "markitdown.converters.docx",
        "markitdown.converters.pptx",
        "markitdown.converters.xlsx",
        "markitdown.converters.html",
        "markitdown.converters.csv",
        "markitdown.converters.json",
        "markitdown.converters.xml",
        "markitdown.converters.txt",
        "markitdown.converters.image",
        "markitdown.converters.audio",
        "markitdown.converters.epub",
        "markitdown.converters.zip",
        "markitdown.converters.outlook_msg",
        "markitdown.converters.youtube",
        "markitdown.converters.az_doc_intel",
        "markitdown.converters.az_content_understanding",
        # Common heavy dependencies that PyInstaller sometimes misses.
        "pdfminer",
        "pdfminer.high_level",
        "pdfminer.pdfinterp",
        "pymupdf",
        "mammoth",
        "openpyxl",
        "pptx",
        "speech_recognition",
        "pydub",
        "youtube_transcript_api",
        "ebooklib",
        "bs4",
        "lxml",
        "PIL",
        "PIL.Image",
        # PySide6 extras that PyInstaller sometimes drops.
        "PySide6.QtXml",
        "PySide6.QtNetwork",
        "PySide6.QtPrintSupport",
        "openai",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy.tests",
        "scipy",
        "pandas",
        "pytest",
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
    name="MarkItDownGUI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon=str(PROJECT_ROOT / "app.ico"),
)