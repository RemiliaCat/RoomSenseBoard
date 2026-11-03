# -*- encoding: utf-8 -*-
r'''
    ____                       _____
   / __ \____  ____  ____ ___ / ___/___  ____  ________
  / /_/ / __ \/ __ \/ __ `__ \\__ \/ _ \/ __ \/ ___/ _ \
 / _, _/ /_/ / /_/ / / / / / /__/ /  __/ / / (__  )  __/
/_/ |_|\____/\____/_/ /_/ /_/____/\___/_/ /_/____/\___/

@File      :   package/roomsenseboard.spec
@Author    :   RmCat
@Contact   :   RemiliaCatNero@gmail.com
@License   :   AGPL
@Copyright :   (C) 2026, RoomSense-Team
@Desc      :   PyInstaller 构建描述：按 ROOMSENSE_ONEFILE 产出单文件或目录形态。
'''

import os
from pathlib import Path

APP_NAME = 'RoomSenseBoard'

# SPECPATH 由 PyInstaller 注入，指向本文件所在目录。
PROJECT_ROOT = Path(SPECPATH).resolve().parent
VERSION_FILE = Path(SPECPATH).resolve() / 'version_info.txt'
ICON_FILE = PROJECT_ROOT / 'web' / 'icon.ico'

ONEFILE = os.environ.get('ROOMSENSE_ONEFILE', '').strip().lower() in {'1', 'true', 'on', 'yes'}

# 不会用到的标准库模块，排除后包体小几十兆。
EXCLUDES = [
    'tkinter',
    '_tkinter',
    'idlelib',
    'lib2to3',
    'pydoc_data',
    'doctest',
    'unittest',
    'test',
]

analysis = Analysis(
    [str(PROJECT_ROOT / 'main.py')],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        (str(PROJECT_ROOT / 'web'), 'web'),
        (str(PROJECT_ROOT / 'doc'), 'doc'),
        (str(PROJECT_ROOT / 'README.md'), '.'),
        (str(PROJECT_ROOT / 'LICENSE'), '.'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(analysis.pure)

COMMON = dict(
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # 需要终端：打印初始密码与地址，支持 Ctrl+C 停止
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_FILE),
    version=str(VERSION_FILE),
)

if ONEFILE:
    exe = EXE(pyz, analysis.scripts, analysis.binaries, analysis.datas, [], **COMMON)
else:
    exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, **COMMON)
    COLLECT(
        exe,
        analysis.binaries,
        analysis.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=APP_NAME,
    )
