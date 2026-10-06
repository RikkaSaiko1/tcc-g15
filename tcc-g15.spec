# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for tcc-g15 (web-enabled fork).
#
# PySide6's built-in hook copies the ENTIRE Qt binary set, including modules
# this app never touches (QML/Quick, Pdf, the software OpenGL fallback,
# translations). --exclude-module does NOT remove those DLLs, so the pruning
# has to happen here, on the collected binary list.
#
# The app imports only QtCore, QtGui and QtWidgets (verified against src/).

import fnmatch

block_cipher = None

# Qt binaries safe to drop: nothing in src/ references QML, Quick, Pdf,
# SQL, Test, Multimedia or the software OpenGL rasteriser.
_EXCLUDE_PATTERNS = [
    'opengl32sw.dll',          # ~19.7 MB software OpenGL fallback
    'Qt6Quick*.dll',           # QML/Quick stack — app is pure QtWidgets
    'Qt6Qml*.dll',
    'Qt6Pdf*.dll',
    'Qt6Sql*.dll',
    'Qt6Test*.dll',
    'Qt6Multimedia*.dll',
    'Qt6WebEngine*.dll',
    'Qt6Designer*.dll',
    'Qt6VirtualKeyboard*.dll',
    'Qt6Charts*.dll',
    'Qt6DataVisualization*.dll',
    'Qt6Bluetooth*.dll',
    'Qt6Nfc*.dll',
    'Qt6Positioning*.dll',
    'Qt6Location*.dll',
    'Qt6SerialPort*.dll',
    'Qt6RemoteObjects*.dll',
    'Qt6Sensors*.dll',
    'Qt6TextToSpeech*.dll',
    'Qt6WebSockets*.dll',
    'Qt6WebChannel*.dll',
    'Qt6SpatialAudio*.dll',
    'Qt6Scxml*.dll',
    'Qt6StateMachine*.dll',
    'Qt6Help*.dll',
    'Qt6UiTools*.dll',
    'Qt6PdfWidgets*.dll',
    'Qt6Concurrent*.dll',
    'Qt63D*.dll',
    'Qt6Graphs*.dll',
]

# Qt plugin subdirectories to drop wholesale. `platforms`, `styles`,
# `imageformats` (png) and `iconengines` are kept — the app needs them.
_EXCLUDE_PLUGIN_DIRS = [
    'qmltooling',
    'qmllint',
    'qmlformat',
    'scenegraph',
    'designer',
    'sqldrivers',
    'multimedia',
    'mediaservice',
    'audio',
    'canbus',
    'position',
    'geoservices',
    'texttospeech',
    'webview',
    'virtualkeyboard',
    'renderers',
    'networkinformation',
    'tls',
]


def _is_excluded(dest):
    """Return True if this destination path should be dropped."""
    d = dest.replace('\\', '/')
    name = d.rsplit('/', 1)[-1]

    for pat in _EXCLUDE_PATTERNS:
        if fnmatch.fnmatch(name.lower(), pat.lower()):
            return True

    # Qt translations are ~6.4 MB of UI strings for languages the app
    # never sets; drop them all.
    if '/translations/' in d.lower() or d.lower().startswith('pyside6/translations/'):
        return True

    for sub in _EXCLUDE_PLUGIN_DIRS:
        if f'/{sub}/' in d.lower():
            return True

    return False


a = Analysis(
    ['src/tcc-g15.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('icons/gaugeIcon.png', 'icons'),
        ('icons/gaugeIcon.ico', 'icons'),
        ('tcc_g15_task.xml', '.'),
        ('src/Web/templates', 'Web/templates'),
    ],
    hiddenimports=['PySide6'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtPdf', 'PySide6.QtSql',
        'PySide6.QtTest', 'PySide6.QtNetwork', 'PySide6.QtMultimedia',
        'PySide6.QtWebEngineCore', 'PySide6.QtDesigner',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# --- Prune collected binaries/data ---
a.binaries = TOC([e for e in a.binaries if not _is_excluded(e[0])])
a.datas = TOC([e for e in a.datas if not _is_excluded(e[0])])

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='tcc-g15',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icons/gaugeIcon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='tcc-g15',
)
