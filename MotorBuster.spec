# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('E:\\Users\\ernes\\Desktop\\Electronics\\fedit2\\fedit_2\\assets', 'assets'), ('E:\\Users\\ernes\\Desktop\\Electronics\\fedit2\\fedit_2\\server', 'server')]
binaries = [('E:\\Users\\ernes\\Desktop\\Electronics\\fedit2\\fedit_2\\server\\SDL3.dll', '.'), ('E:\\Users\\ernes\\Desktop\\Electronics\\fedit2\\fedit_2\\server\\SDL3_image.dll', '.'), ('E:\\Users\\ernes\\Desktop\\Electronics\\fedit2\\fedit_2\\server\\SDL3_mixer.dll', '.'), ('E:\\Users\\ernes\\Desktop\\Electronics\\fedit2\\fedit_2\\server\\SDL3_net.dll', '.'), ('E:\\Users\\ernes\\Desktop\\Electronics\\fedit2\\fedit_2\\server\\SDL3_rtf.dll', '.'), ('E:\\Users\\ernes\\Desktop\\Electronics\\fedit2\\fedit_2\\server\\SDL3_ttf.dll', '.')]
hiddenimports = ['server.ffb_engine']
hiddenimports += collect_submodules('server')
tmp_ret = collect_all('dearpygui')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('dearpygui')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pysdl2_dll')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['E:\\Users\\ernes\\Desktop\\Electronics\\fedit2\\fedit_2\\native_app.py'],
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
    [],
    exclude_binaries=True,
    name='MotorBuster',
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
    icon=['E:\\Users\\ernes\\Desktop\\Electronics\\fedit2\\fedit_2\\assets\\icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MotorBuster',
)
