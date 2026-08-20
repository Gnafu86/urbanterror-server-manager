# PyInstaller spec, shared by the Linux and Windows release builds.
#
# Qt ships far more than this app uses. Excluding the unused modules keeps the
# bundle to a sensible size -- pulling in QtWebEngine alone would roughly triple
# it -- and avoids shipping libraries the app never loads.

import sys
from pathlib import Path

BLOCK_CIPHER = None
ROOT = Path(SPECPATH).resolve().parent
WINDOWS = sys.platform.startswith("win")

analysis = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore",
        "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSql",
        "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
        "tkinter", "matplotlib", "numpy", "PIL",
    ],
    noarchive=False,
    cipher=BLOCK_CIPHER,
)

pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=BLOCK_CIPHER)

# Windows gets a single self-contained .exe; Linux gets a directory that the
# AppImage step wraps, which starts faster than a one-file bundle because it
# does not unpack itself to a temporary directory on every launch.
if WINDOWS:
    exe = EXE(
        pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        name="UrbanTerrorServerManager",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon=None,
    )
else:
    exe = EXE(
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name="UrbanTerrorServerManager",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
    )
    COLLECT(
        exe,
        analysis.binaries,
        analysis.datas,
        strip=False,
        upx=False,
        name="UrbanTerrorServerManager",
    )
