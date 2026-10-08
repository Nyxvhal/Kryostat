# -*- mode: python ; coding: utf-8 -*-
import os
a = Analysis(
    ['main.py'],
    pathex=[os.path.abspath('.')],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=['psutil', 'core.sampler_proc', 'multiprocessing', 'PySide6.QtSvg', 'PySide6.QtNetwork',
                   # модули, которые подключаются лениво (--shell-host) или по требованию
                   'core.shellproc', 'core.defender', 'core.regtools', 'core.syslimit',
                   'core.sysinfo', 'core.usage',
                   'app.palette', 'app.i18n_qt', 'core.i18n',
                   'app.page_apps', 'app.page_dashboard', 'app.page_defender', 'app.page_limits', 'app.page_processes', 'app.page_registry', 'app.page_settings', 'app.page_startup', 'app.page_system', 'app.page_terminal', 'app.page_tweaks', 'app.page_updates'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'PySide6.QtWebEngineCore',
              'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.Qt3DCore',
              'PySide6.QtMultimedia', 'PySide6.QtCharts', 'PySide6.QtDataVisualization'],
    win_no_prefer_redirects=False, win_private_assemblies=False,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='Kryostat',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,   # UPX портит Qt-библиотеки и чаще вызывает ложные срабатывания антивирусов
    console=False,
    disable_windowed_traceback=False,
    icon='assets/kryostat.ico',
    version='version_info.txt',
    uac_admin=True,          # сразу запрашивать права администратора
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False,
    name='Kryostat',
)
