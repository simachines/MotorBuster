# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('c:\\Users\\ernes\\.gemini\\antigravity\\scratch\\fedit_2\\server', 'server')]
binaries = [('c:\\Users\\ernes\\.gemini\\antigravity\\scratch\\fedit_2\\server\\SDL3.dll', '.'), ('c:\\Users\\ernes\\.gemini\\antigravity\\scratch\\fedit_2\\server\\SDL3_image.dll', '.'), ('c:\\Users\\ernes\\.gemini\\antigravity\\scratch\\fedit_2\\server\\SDL3_mixer.dll', '.'), ('c:\\Users\\ernes\\.gemini\\antigravity\\scratch\\fedit_2\\server\\SDL3_net.dll', '.'), ('c:\\Users\\ernes\\.gemini\\antigravity\\scratch\\fedit_2\\server\\SDL3_rtf.dll', '.'), ('c:\\Users\\ernes\\.gemini\\antigravity\\scratch\\fedit_2\\server\\SDL3_ttf.dll', '.')]
hiddenimports = ['server.ffb_engine']
hiddenimports += collect_submodules('server')
tmp_ret = collect_all('dearpygui')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('dearpygui')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pysdl2_dll')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['c:\\Users\\ernes\\.gemini\\antigravity\\scratch\\fedit_2\\native_app.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['uvicorn', 'fastapi', 'starlette', 'tkinter'],
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
    name='Fedit2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
