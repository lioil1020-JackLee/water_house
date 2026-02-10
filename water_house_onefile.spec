# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['water_house.py'],
    pathex=[],
    binaries=[],
    datas=[('img', 'img'), ('ui', 'ui'), ('OPC UA tag.csv', 'opc_tags.csv')],
    hiddenimports=['asyncio', 'asyncua', 'asyncua.ua', 'PyQt6.QtMultimedia'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='water_house',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['img\\享溫泉.ico'],
)