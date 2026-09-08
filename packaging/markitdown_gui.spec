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
        "markitdown.converters._pdf_converter",
        "markitdown.converters._docx_converter",
        "markitdown.converters._pptx_converter",
        "markitdown.converters._xlsx_converter",
        "markitdown.converters._html_converter",
        "markitdown.converters._csv_converter",
        "markitdown.converters._plain_text_converter",
        "markitdown.converters._image_converter",
        "markitdown.converters._audio_converter",
        "markitdown.converters._epub_converter",
        "markitdown.converters._zip_converter",
        "markitdown.converters._outlook_msg_converter",
        "markitdown.converters._youtube_converter",
        "markitdown.converters._doc_intel_converter",
        "markitdown.converters._cu_converter",
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
        "markdown",
        "latex2mathml",
        "keyring",
        "keyring.backends.Windows",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
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
