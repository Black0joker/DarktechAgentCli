# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['textual', 'app', 'app.agent', 'app.widgets', 'app.screens', 'app.layouts', 'app.state', 'app.theme', 'app.utils', 'app.animations', 'app.commands', 'selenium', 'selenium.webdriver', 'selenium.webdriver.chrome.options', 'selenium.webdriver.chrome.service', 'selenium.webdriver.chrome.webdriver', 'selenium.webdriver.common.by', 'selenium.webdriver.common.keys', 'selenium.webdriver.common.action_chains', 'selenium.webdriver.support.ui', 'selenium.webdriver.support.expected_conditions', 'selenium.webdriver.remote', 'selenium.webdriver.remote.webdriver', 'selenium.webdriver.remote.remote_connection'],
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
    name='DarktechAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='images/app_icon.ico',
)
