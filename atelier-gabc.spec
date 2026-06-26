# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Atelier GABC
# Produces a one-dir bundle (required by Velopack for delta updates).
# Build with:  python build.py  (or: pyinstaller atelier-gabc.spec)

import sys

a = Analysis(
    ['app/app.py'],
    pathex=[],
    binaries=[],
    datas=[
        # App assets bundled into sys._MEIPASS
        ('app/static',    'static'),
        ('app/templates', 'templates'),
        ('app/defaults',  'defaults'),
        # Version source of truth
        ('pyproject.toml', '.'),
    ],
    hiddenimports=[
        'velopack',
        'velopack.sources',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AtelierGABC',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX not supported on macOS arm64; disabled for safety
    console=False,      # no terminal window on any platform
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='AtelierGABC',
)

# macOS: wrap the collected dir into a .app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='Atelier GABC.app',
        icon=None,              # TODO: set to 'app/icon.icns' once you have one
        bundle_identifier='com.bethleemapp.atelier-gabc',
        info_plist={
            'NSHighResolutionCapable': True,
            'LSUIElement': False,
        },
    )
