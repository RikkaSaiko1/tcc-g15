# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the PORTABLE single-file tcc-g15 build.
# Shares the same Qt pruning as tcc-g15.spec (see that file for rationale),
# but collapses everything into one exe via EXE(... a.binaries ...).
#
# Built with --name tcc-g15-portable so it does not clash with the ONEDIR
# output in dist/tcc-g15/ that the installer consumes.

import fnmatch

block_cipher = None

_EXCLUDE_PATTERNS = [
    'opengl32sw.dll',
    'Qt6Quick*.dll',
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
    # Confirmed unused by grep over src/ (0 references): the app talks to WMI
    # and serves plain HTTP, so it needs neither Qt networking nor OpenGL/SVG
    # rendering; urllib handles the optional webhook POSTs.
    'Qt6Network*.dll',
    'Qt6OpenGL*.dll',
    'Qt6OpenGLWidgets*.dll',
    'Qt6Svg*.dll',
    'Qt6SvgWidgets*.dll',
    'Qt6PrintSupport*.dll',
    'Qt6Xml*.dll',
    'Qt6DBus*.dll',
]

_EXCLUDE_PLUGIN_DIRS = [
    'qmltooling', 'qmllint', 'qmlformat', 'scenegraph', 'designer',
    'sqldrivers', 'multimedia', 'mediaservice', 'audio', 'canbus',
    'position', 'geoservices', 'texttospeech', 'webview',
    'virtualkeyboard', 'renderers', 'networkinformation', 'tls',
]


_EXCLUDE_PLUGIN_FILES = [
    'qdirect2d.dll',
    'qminimald.dll',
    'qoffscreend.dll',
    'qsvgicon.dll',      # SVG icon engine — app uses .ico/.png
    'qpdf.dll',
    'qtga.dll',          # only png/ico/jpeg are used
    'qwbmp.dll',
    'qicns.dll',
    'qtiff.dll',
    'qgif.dll',
]

def _is_excluded(dest):
    d = dest.replace('\\', '/')
    name = d.rsplit('/', 1)[-1]
    for pat in _EXCLUDE_PATTERNS:
        if fnmatch.fnmatch(name.lower(), pat.lower()):
            return True
    if '/translations/' in d.lower():
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

a.binaries = TOC([e for e in a.binaries if not _is_excluded(e[0])])
a.datas = TOC([e for e in a.datas if not _is_excluded(e[0])])

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='tcc-g15-portable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icons/gaugeIcon.ico',
)
