# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['jarvis_app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('gui', 'gui'),
        ('default_data', 'default_data'),
        ('build/build_identity.json', '.'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'cv2', 'numpy', 'PIL', 'tkinter', 'textual', 'pyautogui',
        # Pywinauto imports win32ui from base_wrapper during normal startup.
        # Only the standalone trace extension is unused by the runtime.
        'win32trace',
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Jarvis',
    version='build/Jarvis_version_info.txt',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is not part of the locked build toolchain. Keep this explicit so a
    # machine-local UPX installation cannot silently change release artifacts.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Jarvis',
)
